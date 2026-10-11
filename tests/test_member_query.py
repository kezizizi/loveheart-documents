"""M3 切片（下）：单成员跨库查询与参与判定。"""

from __future__ import annotations

import pytest

from config import SENSITIVE_FIELDS
from feishu.client import SchemaChangedError
from feishu.member_query import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    NOT_FOUND_NAME,
    NOT_FOUND_SID,
    NOT_FOUND_SID_MISSING,
    collect_participations,
    judge_confidence,
    lookup_member,
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
    recs_25 = lookup_member(client, spec_25, registry, "张三", "2300017473").records
    recs_26 = lookup_member(client, spec_26, registry, "张三", "2300017473").records

    assert [r.record_id for r in recs_25] == ["recM25"]
    assert [r.record_id for r in recs_26] == ["recM26"]


def test_find_member_does_not_confuse_same_name(specs):
    """同名不同学号的人绝不能被匹配进来。"""
    client, registry = _registry_and_client(specs)
    spec_26 = next(s for s in specs if s.key == "base_26_27")

    recs = lookup_member(client, spec_26, registry, "张三", "2300017473").records
    assert all(r.record_id != "recM27c" for r in recs)


def test_find_member_returns_empty_when_absent(specs):
    client, registry = _registry_and_client(specs)
    spec_26 = next(s for s in specs if s.key == "base_26_27")
    assert lookup_member(client, spec_26, registry, "查无此人", "1").records == ()


# --------------------------------------------------------------------------
# 查不到的原因诊断（必须复用已有检索结果，不得额外发请求）
# --------------------------------------------------------------------------


def test_lookup_reason_is_none_when_found(specs):
    client, registry = _registry_and_client(specs)
    spec_25 = next(s for s in specs if s.key == "base_25_26")
    assert lookup_member(client, spec_25, registry, "张三", "2300017473").reason is None


def test_lookup_reports_name_not_in_archive(specs):
    client, registry = _registry_and_client(specs)
    spec_25 = next(s for s in specs if s.key == "base_25_26")

    look = lookup_member(client, spec_25, registry, "查无此人", "1")

    assert look.records == ()
    assert look.reason == NOT_FOUND_NAME


def test_lookup_reports_sid_mismatch(specs):
    """姓名在档案里，但登记的学号跟输入的对不上。"""
    client, registry = _registry_and_client(specs)
    spec_26 = next(s for s in specs if s.key == "base_26_27")

    look = lookup_member(client, spec_26, registry, "李四", "9999999999")

    assert look.records == ()
    assert look.reason == NOT_FOUND_SID


def test_lookup_reports_missing_sid_on_archive(specs):
    """姓名命中但那条档案压根没填学号 —— 只能靠补录解决。"""
    client, registry = _registry_and_client(specs)
    spec_25 = next(s for s in specs if s.key == "base_25_26")

    look = lookup_member(client, spec_25, registry, "赵无号", "2300010000")

    assert look.records == ()
    assert look.reason == NOT_FOUND_SID_MISSING


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
        records.extend(
            lookup_member(client, spec, registry, "张三", "2300017473").records
        )
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


# --------------------------------------------------------------------------
# 关联快通道失效时必须退化成扫表
# --------------------------------------------------------------------------


def _strip_main_links(client) -> None:
    """模拟「主表关联列一个都没填」的坏数据状态。"""
    for (base_token, table_id), rows in client.records.items():
        if table_id not in ("tblE5QYLVyf7YBmE", "tbliPIl5lYefS4Nr"):
            continue
        for row in rows:
            fields = row.get("fields") or {}
            for key in list(fields):
                if key in ("姓名", "学号", "性别", "年级", "院系", "入社日期", "入社时间"):
                    continue
                value = fields[key]
                if isinstance(value, dict) and (
                    "link_record_ids" in value or "record_ids" in value
                ):
                    fields.pop(key)
                elif isinstance(value, list) and value and isinstance(value[0], dict) and (
                    "record_ids" in value[0]
                ):
                    fields.pop(key)


def test_query_member_falls_back_to_scan_when_links_empty(specs):
    """主表关联列全空时，必须逐表扫描，绝不能因此漏掉参与记录。"""
    from tests.conftest import build_fixture_client

    client = build_fixture_client(records_file="records.json")
    _strip_main_links(client)
    registry = build_registry(client, specs)

    result = query_member(client, registry, specs, "张三", "2300017473")

    assert result.found is True
    names = {p.activity_name for p in result.participations}
    assert "26秋-安宁疗护" in names, "关联列失效时仍必须扫出活动"
    assert "2025秋金盲杖" in names
    # 活动记录自身的 link 指向本人 → 仍然可以判为高置信
    auhen = [p for p in result.participations if p.table_id == "tblp8GDapgvtG4wP"]
    assert auhen and auhen[0].confidence == "high"


def test_query_member_not_found(specs):
    client, registry = _registry_and_client(specs)
    result = query_member(client, registry, specs, "不存在的人", "999")

    assert result.found is False
    assert result.member is None
    assert result.participations == ()
    assert result.not_found_reason == NOT_FOUND_NAME


def test_query_member_prefers_sid_missing_reason(specs):
    """「档案里查到姓名但没填学号」比「查不到姓名」更可行动，优先级应更高。

    「赵无号」只在 25-26 有档案且没填学号，26-27 连姓名都没有 ——
    必须归因到「缺学号」，而不是笼统的「查不到」。
    """
    client, registry = _registry_and_client(specs)
    result = query_member(client, registry, specs, "赵无号", "2300010000")

    assert result.found is False
    assert result.not_found_reason == NOT_FOUND_SID_MISSING


def test_query_member_reason_is_none_when_lookup_itself_failed(specs):
    """所有库的检索都报错时，不能编一个「姓名查不到」出来。

    那种情况下我们**根本不知道**人家在不在档案里，瞎归因会把人引到补录问卷，
    而问题其实出在系统这一侧。
    """
    client, registry = _registry_and_client(specs)

    def boom(*_args, **_kwargs):
        raise RuntimeError("网络炸了")

    client.search_records = boom  # type: ignore[method-assign]

    result = query_member(client, registry, specs, "张三", "2300017473")

    assert result.found is False
    assert result.not_found_reason is None
    assert result.errors, "检索失败必须被记录下来，不能悄悄退化成「查无此人」"


# --------------------------------------------------------------------------
# 🚩 红线：遍历范围
# --------------------------------------------------------------------------


def test_collect_participations_never_widens_scope(specs):
    """遍历范围必须**严格等于** member.base_keys。

    活动表扫描是查询里最贵的一步。只在一个库有主表记录的人，
    绝不去扫另一个库的活动表 —— 飞书 API 调用量有月度上限，
    擅自扩大范围（例如「反正多扫一点更全」）会把配额瞬间吃光。
    这条是写死的红线，任何「顺手优化」都不得突破。
    """
    from services.identity import merge_members

    client, registry = _registry_and_client(specs)
    spec_25 = next(s for s in specs if s.key == "base_25_26")

    member = merge_members(
        list(lookup_member(client, spec_25, registry, "钱只二五", "2300018888").records)
    )
    assert member is not None
    assert member.base_keys == ("base_25_26",), "前提：此人只在一个库有主表记录"

    client.calls.clear()
    collect_participations(client, registry, member)

    touched = {
        call[1]
        for call in client.calls
        if call[0] in ("search_records", "batch_get_records")
    }
    assert touched == {BASE_25_26}, (
        f"红线：不得扩大遍历范围（飞书 API 有月度配额），实际触碰了 {touched}"
    )


# --------------------------------------------------------------------------
# 结构变更必须穿透「单表失败隔离」
# --------------------------------------------------------------------------


def _make_schema_changed(client):
    def boom(*_args, **_kwargs):
        raise SchemaChangedError("活动表结构已变更（字段被改名或删除）")

    client.search_records = boom  # type: ignore[method-assign]
    client.batch_get_records = boom  # type: ignore[method-assign]
    return client


def test_lookup_propagates_schema_change(specs):
    """结构变更必须穿透兜底尝试的宽泛 except。

    否则会被当成「这张表读不到」静默吞掉，
    上层的「重建注册表再试一次」永远触发不了。
    """
    client, registry = _registry_and_client(specs)
    spec_25 = next(s for s in specs if s.key == "base_25_26")
    _make_schema_changed(client)

    with pytest.raises(SchemaChangedError):
        lookup_member(client, spec_25, registry, "张三", "2300017473")


def test_collect_propagates_schema_change(specs):
    client, registry = _registry_and_client(specs)
    merged = _merged(client, registry, specs)
    _make_schema_changed(client)

    with pytest.raises(SchemaChangedError):
        collect_participations(client, registry, merged)


def test_query_propagates_schema_change(specs):
    client, registry = _registry_and_client(specs)
    _make_schema_changed(client)

    with pytest.raises(SchemaChangedError):
        query_member(client, registry, specs, "张三", "2300017473")


def test_collect_isolates_single_table_failure(specs):
    from tests.conftest import build_fixture_client
    from services.identity import merge_members

    client = build_fixture_client(
        records_file="records.json", fail_records=["tbl6CT22uQUGe7gV"]
    )
    registry = build_registry(client, specs)
    records = []
    for spec in specs:
        records.extend(
            lookup_member(client, spec, registry, "张三", "2300017473").records
        )
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
