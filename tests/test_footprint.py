"""M4 切片：入社日期口径、时间轴排序、学时汇总、聚合、同行统计。"""

from __future__ import annotations

from datetime import datetime

from feishu.models import MemberRecord, Participation
from services.footprint import (
    build_timeline,
    count_co_partners,
    group_breakdown,
    summarize,
    timeline_sort_key,
)
from services.membership_date import (
    days_since,
    derive_membership_date,
    format_date,
    join_term_hint,
)
from services.identity import merge_members
from tests.conftest import build_fixture_client


def _rec(base_key, record_id, created_ms=None, **fields):
    return MemberRecord(
        base_key=base_key,
        base_token=f"token-{base_key}",
        table_id="main",
        record_id=record_id,
        name="张三",
        sid="2300017473",
        raw_fields=fields,
        created_time_ms=created_ms,
    )


def _patient(table_id="t1", name="活动A", group="护老部", term=None, **kw):
    return Participation(
        base_key="base_26_27",
        table_id=table_id,
        activity_name=name,
        group=group,
        term=term,
        record_id=kw.pop("record_id", f"rec-{table_id}-{name}"),
        **kw,
    )


# --------------------------------------------------------------------------
# 入社日期口径
# --------------------------------------------------------------------------


def test_membership_date_prefers_real_field():
    member = merge_members(
        [
            _rec("base_25_26", "r1", 1_800_000_000_000, 入社日期=1_696_032_000_000),
            _rec("base_26_27", "r2", 1_750_000_000_000),
        ]
    )
    assert member is not None
    result = derive_membership_date(member)

    assert result.date_ms == 1_696_032_000_000
    assert result.is_estimated is False
    assert "入社日期" in result.source


def test_membership_date_falls_back_to_created_time_and_marks_estimated():
    """26-27 库没有日期字段 → 用记录创建时间，并且必须标成估算。"""
    member = merge_members(
        [
            _rec("base_26_27", "r2", 1_750_000_000_000, 入社时间=[{"text": "26秋"}]),
        ]
    )
    assert member is not None
    result = derive_membership_date(member)

    assert result.date_ms == 1_750_000_000_000
    assert result.is_estimated is True


def test_membership_date_takes_earliest_across_bases():
    member = merge_members(
        [
            _rec("base_25_26", "r1", 1_700_000_000_000, 入社日期=1_700_000_000_000),
            _rec("base_26_27", "r2", 1_750_000_000_000),
        ]
    )
    assert member is not None
    assert derive_membership_date(member).date_ms == 1_700_000_000_000


def test_membership_date_unknown():
    member = merge_members([_rec("base_26_27", "r2")])
    assert member is not None
    result = derive_membership_date(member)

    assert result.date_ms is None
    assert result.is_estimated is True
    assert result.source == "未记录"


def test_format_date_and_days_since():
    assert format_date(1_696_032_000_000) == "2023.09.30"
    assert format_date(None) == ""

    now = datetime(2026, 10, 11)
    assert days_since(1_696_032_000_000, now=now) == 1107
    assert days_since(None) is None


def test_join_term_hint_reads_select_option():
    member = merge_members(
        [_rec("base_26_27", "r2", 1, 入社时间=[{"text": "23秋以前"}])]
    )
    assert member is not None
    assert join_term_hint(member) == "23秋以前"


# --------------------------------------------------------------------------
# 时间轴排序：三级回退
# --------------------------------------------------------------------------


def test_timeline_sort_prefers_event_time():
    early = _patient(event_time_ms=1000)
    late = _patient(table_id="t2", name="活动B", event_time_ms=2000)
    assert timeline_sort_key(early) < timeline_sort_key(late)


def test_timeline_sort_falls_back_to_term_then_created():
    has_time = _patient(event_time_ms=999_999_999_999)
    by_term = _patient(table_id="t2", name="B", term="25秋")
    by_created = _patient(table_id="t3", name="C", record_created_ms=1)
    unknown = _patient(table_id="t4", name="D")

    ordered = sorted([unknown, by_created, by_term, has_time], key=timeline_sort_key)
    assert [p.activity_name for p in ordered] == ["活动A", "B", "C", "D"]


def test_timeline_sort_orders_terms_chronologically():
    a = _patient(table_id="t1", name="春", term="2025春")
    b = _patient(table_id="t2", name="暑", term="2025暑")
    c = _patient(table_id="t3", name="秋", term="2025秋")
    d = _patient(table_id="t4", name="次年", term="2026春")

    ordered = sorted([d, c, a, b], key=timeline_sort_key)
    assert [p.activity_name for p in ordered] == ["春", "暑", "秋", "次年"]


def test_build_timeline_produces_items():
    timeline = build_timeline([_patient(event_time_ms=5000)])
    assert len(timeline) == 1
    assert timeline[0].activity_name == "活动A"
    assert timeline[0].event_time_ms == 5000


# --------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------


def test_summarize_sums_all_hours_fields():
    parts = [
        _patient(table_id="t1", name="A", hours=3.5),
        _patient(table_id="t2", name="B", hours=2.0),
        _patient(table_id="t3", name="C", hours=0.5),
    ]
    fp = summarize(parts)

    assert fp.total_hours == 6.0
    assert fp.activity_count == 3
    assert fp.distinct_activity_count == 3


def test_summarize_counts_distinct_activities_vs_records():
    parts = [
        _patient(table_id="t1", name="A", hours=1, record_id="r1"),
        _patient(table_id="t1", name="A", hours=1, record_id="r2"),
    ]
    fp = summarize(parts)

    assert fp.activity_count == 2
    assert fp.distinct_activity_count == 1


def test_summarize_groups_and_term_breakdown():
    parts = [
        _patient(table_id="t1", name="A", group="护老部", term="2025秋", hours=2),
        _patient(table_id="t2", name="B", group="无障碍部", term="2026春", hours=3),
        _patient(table_id="t3", name="C", group=None, term=None, hours=1),
    ]
    fp = summarize(parts)

    assert set(fp.groups) == {"护老部", "无障碍部", "其他"}
    assert fp.hour_by_group["护老部"] == 2.0
    assert list(fp.count_by_term) == ["2025秋", "2026春", "未标注学期"]


def test_summarize_flags_low_confidence():
    parts = [
        _patient(table_id="t1", name="A", confidence="low"),
        _patient(table_id="t2", name="B", confidence="high"),
    ]
    assert summarize(parts).confidence_low == 1


def test_summarize_empty():
    fp = summarize([])
    assert fp.total_hours == 0.0
    assert fp.activity_count == 0
    assert fp.groups == ()


def test_group_breakdown_has_colors():
    parts = [
        _patient(table_id="t1", name="A", group="护老部", hours=2),
        _patient(table_id="t2", name="B", group="护老部", hours=1),
    ]
    breakdown = group_breakdown(parts)

    assert len(breakdown) == 1
    assert breakdown[0]["group"] == "护老部"
    assert breakdown[0]["hours"] == 3.0
    assert breakdown[0]["count"] == 2
    assert breakdown[0]["color"].startswith("#")


# --------------------------------------------------------------------------
# 与 N 位伙伴同行
# --------------------------------------------------------------------------


def test_count_co_partners_unions_and_excludes_self(specs):
    client = build_fixture_client(records_file="records.json")
    from feishu.member_query import query_member
    from feishu.registry import build_registry

    registry = build_registry(client, specs)
    result = query_member(client, registry, specs, "张三", "2300017473")
    assert result.member is not None

    count, truncated = count_co_partners(
        client, registry, result.member, result.participations
    )

    assert truncated is False
    # 张三参与过的活动表里出现过的其他姓名只有「李四」。
    # 特别注意：安宁疗护表里有个同名「张三」（学号不同、link 指向别人），
    # 他必须被当成「自己」排除掉，不能算成一个伙伴。
    assert count == 1


def test_count_co_partners_truncates_when_over_cap(specs):
    client = build_fixture_client(records_file="records.json")
    from feishu.member_query import query_member
    from feishu.registry import build_registry

    registry = build_registry(client, specs)
    result = query_member(client, registry, specs, "张三", "2300017473")
    assert result.member is not None

    count, truncated = count_co_partners(
        client, registry, result.member, result.participations, cap=1
    )

    assert truncated is True
    assert count is not None


def test_count_co_partners_survives_table_failure(specs):
    client = build_fixture_client(
        records_file="records.json", fail_records=["tblp8GDapgvtG4wP"]
    )
    from feishu.member_query import query_member
    from feishu.registry import build_registry

    registry = build_registry(client, specs)
    result = query_member(client, registry, specs, "张三", "2300017473")
    assert result.member is not None

    count, _ = count_co_partners(client, registry, result.member, result.participations)
    assert count is not None
