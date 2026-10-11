"""M3 切片（下）：单成员跨库查询与参与判定。"""

from __future__ import annotations

from config import SENSITIVE_FIELDS
from feishu.member_query import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    collect_participations,
    find_member_records,
    judge_confidence,
    main_projection,
    query_member,
)
from feishu.registry import build_registry
from tests.conftest import BASE_25_26, BASE_26_27


def _registry_and_client(specs):
    from tests.conftest import build_fixture_client

    client = build_fixture_client(records_file="records.json")
    return client, build_registry(client, specs)


# --------------------------------------------------------------------------
# 字段投影
# --------------------------------------------------------------------------


def test_main_projection_contains_identity_and_link_names(specs):
    client, registry = _registry_and_client(specs)
    spec = next(s for s in specs if s.key == "base_26_27")
    projection = main_projection(registry, spec)

    assert "姓名" in projection
    assert "学号" in projection
    assert "26秋-安宁疗护-反馈问卷" in projection
    # 自关联字段不属于任何活动，不应出现在投影里
    assert "父记录" not in projection


def test_main_projection_never_contains_sensitive_fields(specs):
    client, registry = _registry_and_client(specs)
    for spec in specs:
        projection = set(main_projection(registry, spec))
        assert not (projection & SENSITIVE_FIELDS), f"{spec.label} 投影泄露敏感字段"


# --------------------------------------------------------------------------
# 找主表记录
# --------------------------------------------------------------------------


def test_find_member_across_number_and_text_sid(specs):
    client, registry = _registry_and_client(specs)

    spec_25 = next(s for s in specs if s.key == "base_25_26")
    spec_26 = next(s for s in specs if s.key == "base_26_27")

    # 25-26 库学号是数字，26-27 库学号是文本 —— 传字符串两边都要命中
    recs_25 = find_member_records(client, spec_25, registry, "张三", "2300017473")
    recs_26 = find_member_records(client, spec_26, registry, "张三", "2300017473")

    assert [r.record_id for r in recs_25] == ["recM25"]
    assert [r.record_id for r in recs_26] == ["recM26"]


def test_find_member_does_not_confuse_same_name(specs):
    """同名不同学号的人绝不能被匹配进来。"""
    client, registry = _registry_and_client(specs)
    spec_26 = next(s for s in specs if s.key == "base_26_27")

    recs = find_member_records(client, spec_26, registry, "张三", "2300017473")
    assert all(r.record_id != "recM27c" for r in recs)


def test_find_member_returns_empty_when_absent(specs):
    client, registry = _registry_and_client(specs)
    spec_26 = next(s for s in specs if s.key == "base_26_27")
    assert find_member_records(client, spec_26, registry, "查无此人", "1") == []


# --------------------------------------------------------------------------
# 参与判定优先级
# --------------------------------------------------------------------------


def test_judge_link_record_id_beats_name(specs):
    """link 指向别人时，即使同名也必须判为「不是本人」。"""
    client, registry = _registry_and_client(specs)
    merged = _merged(client, registry, specs)
    auhen = registry.by_table_id()["tblp8GDapgvtG4wP"]

    other = {
        "record_id": "recA3",
        "fields": {"姓名": [{"text": "张三"}], "社员档案": [{"record_ids": ["recM27c"]}]},
    }
    assert judge_confidence(auhen, other, merged) is None

    mine = {
        "record_id": "recA1",
        "fields": {"姓名": [{"text": "张三"}], "社员档案": [{"record_ids": ["recM26"]}]},
    }
    assert judge_confidence(auhen, mine, merged) == CONFIDENCE_HIGH


def test_judge_falls_back_to_name_only_when_link_empty(specs):
    client, registry = _registry_and_client(specs)
    merged = _merged(client, registry, specs)
    orphan = registry.by_table_id()["tbl6CT22uQUGe7gV"]

    row = {
        "record_id": "recD2",
        "fields": {"姓名": [{"text": "张三"}], "社员档案": []},
    }
    assert judge_confidence(orphan, row, merged) == CONFIDENCE_LOW


def test_judge_uses_sid_when_available(specs):
    client, registry = _registry_and_client(specs)
    merged = _merged(client, registry, specs)
    jinmang = registry.by_table_id()["tblB74WxX7708aKd"]

    row = {
        "record_id": "recJ1",
        "fields": {"姓名": [{"text": "张三"}], "学号": 2300017473, "社员档案": []},
    }
    assert judge_confidence(jinmang, row, merged) == "medium"


def _merged(client, registry, specs):
    from services.identity import merge_members

    records = []
    for spec in specs:
        records.extend(find_member_records(client, spec, registry, "张三", "2300017473"))
    return merge_members(records)


# --------------------------------------------------------------------------
# 端到端
# --------------------------------------------------------------------------


def test_query_member_merges_two_bases(specs):
    client, registry = _registry_and_client(specs)
    result = query_member(client, registry, specs, "张三", "2300017473")

    assert result.found is True
    assert result.member is not None
    assert result.member.in_multiple_bases is True
    assert set(result.searched_bases) == {"base_25_26", "base_26_27"}


def test_query_member_collects_expected_activities(specs):
    client, registry = _registry_and_client(specs)
    result = query_member(client, registry, specs, "张三", "2300017473")
    names = {p.activity_name for p in result.participations}

    assert "26秋-安宁疗护" in names
    assert "26秋-敬老院" in names
    assert "2025秋金盲杖" in names
    assert "2025秋扫雪" in names
    assert "26秋-守望星空夏令营影展" in names


def test_query_member_does_not_leak_same_name_person_hours(specs):
    """同名者那笔 99 小时绝不能算进来。"""
    client, registry = _registry_and_client(specs)
    result = query_member(client, registry, specs, "张三", "2300017473")

    auhen = [p for p in result.participations if p.table_id == "tblp8GDapgvtG4wP"]
    assert len(auhen) == 1
    assert auhen[0].hours == 3.5
    assert all(p.hours != 99 for p in result.participations)


def test_query_member_excludes_other_peoples_records(specs):
    client, registry = _registry_and_client(specs)
    result = query_member(client, registry, specs, "张三", "2300017473")
    assert all(p.record_id not in {"recA2", "recA3", "recO1"} for p in result.participations)


def test_query_member_never_touches_forbidden_table(specs):
    client, registry = _registry_and_client(specs)
    query_member(client, registry, specs, "张三", "2300017473")
    touched = {call[2] for call in client.calls if len(call) >= 3}
    assert "tbleCOz8lHcOHXvs" not in touched


def test_query_member_projections_exclude_sensitive_fields(specs):
    client, registry = _registry_and_client(specs)
    query_member(client, registry, specs, "张三", "2300017473")

    assert client.projections, "应当确实发出过带字段投影的请求"
    for op, table_id, names in client.projections:
        leaked = set(names) & SENSITIVE_FIELDS
        assert not leaked, f"{op} 对 {table_id} 泄露了敏感字段 {leaked}"


def test_query_member_flags_low_confidence(specs):
    client, registry = _registry_and_client(specs)
    result = query_member(client, registry, specs, "张三", "2300017473")

    assert result.low_confidence_count >= 2, "仅有姓名可依据的记录必须标为低置信"


def test_query_member_not_found(specs):
    client, registry = _registry_and_client(specs)
    result = query_member(client, registry, specs, "不存在的人", "999")

    assert result.found is False
    assert result.member is None
    assert result.participations == ()


def test_collect_isolates_single_table_failure(specs):
    from tests.conftest import build_fixture_client
    from services.identity import merge_members

    client = build_fixture_client(
        records_file="records.json", fail_records=["tbl6CT22uQUGe7gV"]
    )
    registry = build_registry(client, specs)
    records = []
    for spec in specs:
        records.extend(find_member_records(client, spec, registry, "张三", "2300017473"))
    merged = merge_members(records)
    assert merged is not None

    parts, errors = collect_participations(client, registry, merged)

    assert any(p.table_id == "tblp8GDapgvtG4wP" for p in parts), "其他表不受影响"
    assert errors and any("守望星空" in e for e in errors)


def test_query_member_survives_one_base_unreadable(specs):
    """26-27 库没加协作者时，仍应查出 25-26 库的部分。"""

    from tests.conftest import FakeBitableClient, load_fixture

    raw_fields = load_fixture("fields.json")
    raw_records = load_fixture("records.json")
    flat_f: dict[tuple[str, str], list] = {}
    flat_r: dict[tuple[str, str], list] = {}
    for raw, target in ((raw_fields, flat_f), (raw_records, flat_r)):
        for base, tables in raw.items():
            for tid, items in tables.items():
                target[(base, tid)] = items

    class HalfBroken(FakeBitableClient):
        def list_blocks(self, base_token, **kwargs):
            if base_token == BASE_26_27:
                raise RuntimeError("模拟 403：未添加协作者")
            return super().list_blocks(base_token, **kwargs)

    client = HalfBroken(
        blocks=load_fixture("blocks.json"), fields=flat_f, records=flat_r
    )
    registry = build_registry(client, specs)
    result = query_member(client, registry, specs, "张三", "2300017473")

    assert result.found is True
    names = {p.activity_name for p in result.participations}
    assert "2025秋金盲杖" in names
    assert "26秋-安宁疗护" not in names
    assert any("26-27" in e for e in result.errors)
