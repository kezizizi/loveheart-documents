"""M3 切片（上）：身份归一化与跨库合并。"""

from __future__ import annotations

from feishu.models import MemberRecord
from services.identity import (
    identity_key,
    merge_members,
    names_equal,
    normalize_name,
    normalize_sid,
    sids_equal,
)


# --------------------------------------------------------------------------
# 学号归一：25-26 库是数字字段，26-27 库是文本字段
# --------------------------------------------------------------------------


def test_normalize_sid_unifies_number_and_text():
    assert normalize_sid(2300017473) == "2300017473"
    assert normalize_sid("2300017473") == "2300017473"
    assert normalize_sid("2300017473.0") == "2300017473"
    assert normalize_sid(2300017473.0) == "2300017473"


def test_normalize_sid_handles_junk():
    assert normalize_sid(None) == ""
    assert normalize_sid("") == ""
    assert normalize_sid(True) == ""
    assert normalize_sid(" 2300017473 ") == "2300017473"


def test_sids_equal_across_field_types():
    """这是整个系统最容易翻车的地方：类型不一致必须仍能匹配。"""
    assert sids_equal(2300017473, "2300017473")
    assert sids_equal("2300017473", 2300017473.0)
    assert not sids_equal("", "2300017473")
    assert not sids_equal(None, None)


def test_normalize_name_removes_all_whitespace():
    assert normalize_name("张 三") == "张三"
    assert normalize_name("  张三\u3000") == "张三"
    assert names_equal("张 三", "张三")
    assert not names_equal("张三", "张四")
    assert not names_equal("", "")


def test_identity_key():
    assert identity_key(2300017473, "张 三") == ("2300017473", "张三")


# --------------------------------------------------------------------------
# 跨库合并
# --------------------------------------------------------------------------


def _rec(base_key, record_id, name, sid, created_ms, **fields):
    return MemberRecord(
        base_key=base_key,
        base_token=f"token-{base_key}",
        table_id=f"main-{base_key}",
        record_id=record_id,
        name=name,
        sid=sid,
        raw_fields=fields,
        created_time_ms=created_ms,
    )


def test_merge_single_record():
    merged = merge_members([_rec("base_26_27", "r1", "张三", "2300017473", 1000)])
    assert merged is not None
    assert merged.base_keys == ("base_26_27",)
    assert merged.in_multiple_bases is False


def test_merge_across_two_bases():
    records = [
        _rec("base_25_26", "r25", "张三", "2300017473", 1_700_000_000_000),
        _rec("base_26_27", "r26", "张三", "2300017473", 1_750_000_000_000),
    ]
    merged = merge_members(records)
    assert merged is not None
    assert merged.in_multiple_bases is True
    assert set(merged.base_keys) == {"base_25_26", "base_26_27"}
    assert merged.record_ids_of("base_25_26") == ("r25",)
    assert merged.record_ids_of("base_26_27") == ("r26",)


def test_value_prefers_newer_record_but_skips_empty():
    records = [
        _rec("base_25_26", "r25", "张三", "2300017473", 1_700_000_000_000, 院系="元培学院"),
        _rec("base_26_27", "r26", "张三", "2300017473", 1_750_000_000_000),
    ]
    merged = merge_members(records)
    assert merged is not None
    # 新记录没有院系 → 落到旧记录的值
    assert merged.value("院系") == "元培学院"
    assert merged.value("不存在") is None


def test_link_targets_merged_across_records():
    a = MemberRecord(
        base_key="base_25_26",
        base_token="t",
        table_id="m",
        record_id="r1",
        name="张三",
        sid="1",
        link_targets={"2025秋金盲杖": ("recJ1",)},
    )
    b = MemberRecord(
        base_key="base_25_26",
        base_token="t",
        table_id="m",
        record_id="r2",
        name="张三",
        sid="1",
        link_targets={"2025秋金盲杖": ("recJ2",), "2025秋扫雪": ("recK1",)},
    )
    merged = merge_members([a, b])
    assert merged is not None
    assert merged.link_targets_of("base_25_26", "2025秋金盲杖") == ("recJ1", "recJ2")
    assert merged.link_targets_of("base_25_26", "2025秋扫雪") == ("recK1",)
    assert merged.link_targets_of("base_26_27", "2025秋金盲杖") == ()


def test_duplicate_records_in_same_base_is_flagged():
    records = [
        _rec("base_25_26", "r1", "张三", "2300017473", 1000),
        _rec("base_25_26", "r2", "张三", "2300017473", 2000),
    ]
    merged = merge_members(records)
    assert merged is not None
    assert merged.has_duplicate_records is True


def test_merge_empty_returns_none():
    assert merge_members([]) is None
