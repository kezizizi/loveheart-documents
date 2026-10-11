"""飞书原始 JSON → 统一形状的归一化测试。

这里放着**真实数据上踩过的坑**，每一条都对应一次线上事故。
"""

from __future__ import annotations

from feishu import schema as S


# --------------------------------------------------------------------------
# link 单元格：键名有坑
# --------------------------------------------------------------------------


def test_link_value_uses_link_record_ids_key():
    """⚠️ 真实飞书返回的键名是 link_record_ids（实测），不是 record_ids。"""
    value = {"link_record_ids": ["recHeNhXO8"]}
    assert S.cell_link_record_ids(value) == ["recHeNhXO8"]


def test_link_value_accepts_legacy_record_ids_key():
    """旧文档/旧样例里的 record_ids 形态也要认。"""
    value = [{"record_ids": ["recA", "recB"], "text": "某个活动"}]
    assert S.cell_link_record_ids(value) == ["recA", "recB"]


def test_link_value_handles_both_keys_in_one_payload():
    value = {"link_record_ids": ["recA"], "record_ids": ["recB"]}
    assert set(S.cell_link_record_ids(value)) == {"recA", "recB"}


def test_link_value_bare_string():
    assert S.cell_link_record_ids("recXYZ") == ["recXYZ"]


def test_link_value_empty_shapes():
    assert S.cell_link_record_ids(None) == []
    assert S.cell_link_record_ids([]) == []
    assert S.cell_link_record_ids({}) == []
    assert S.cell_link_record_ids({"link_record_ids": []}) == []
    assert S.cell_link_record_ids("不是记录号") == []


def test_link_value_dedupes_and_keeps_order():
    value = {"link_record_ids": ["recB", "recA", "recB"]}
    assert S.cell_link_record_ids(value) == ["recB", "recA"]


# --------------------------------------------------------------------------
# 文本 / 数字 / 日期
# --------------------------------------------------------------------------


def test_cell_text_shapes():
    assert S.cell_text([{"text": "张三", "type": "text"}]) == "张三"
    assert S.cell_text("张三") == "张三"
    assert S.cell_text([{"text": "男"}]) == "男"
    assert S.cell_text(["护老部", "无障碍部"]) == "护老部, 无障碍部"
    assert S.cell_text(None) == ""
    assert S.cell_text(2300017473) == "2300017473"
    assert S.cell_text(2300017473.0) == "2300017473"
    assert S.cell_text(True) == "是"


def test_cell_number_shapes():
    assert S.cell_number(3.5) == 3.5
    assert S.cell_number("3.5小时") == 3.5
    assert S.cell_number([{"text": "2"}]) == 2.0
    assert S.cell_number({"value": 4}) == 4.0
    assert S.cell_number(None) is None
    assert S.cell_number("") is None
    assert S.cell_number("无") is None


def test_cell_datetime_ms_promotes_seconds():
    assert S.cell_datetime_ms(1696032000000) == 1696032000000
    assert S.cell_datetime_ms(1696032000) == 1696032000000
    assert S.cell_datetime_ms([{"value": 1696032000000}]) == 1696032000000
    assert S.cell_datetime_ms(None) is None


# --------------------------------------------------------------------------
# 字段归一化
# --------------------------------------------------------------------------


def test_normalize_field_accepts_both_api_shapes():
    v1 = {
        "field_id": "fld1",
        "field_name": "社员档案",
        "type": 21,
        "property": {"table_id": "tblMain"},
    }
    v3 = {"id": "fld1", "name": "社员档案", "type": 21, "property": {"link_table": "tblMain"}}

    for raw in (v1, v3):
        field = S.normalize_field(raw)
        assert field["id"] == "fld1"
        assert field["name"] == "社员档案"
        assert field["is_link"] is True
        assert field["link_table_id"] == "tblMain"


def test_normalize_fields_reads_records_key_variants():
    assert len(S.normalize_fields({"fields": [{"field_id": "a", "field_name": "A"}]})) == 1
    assert len(S.normalize_fields({"items": [{"field_id": "a", "field_name": "A"}]})) == 1
    assert S.normalize_fields(None) == []


def test_normalize_record_reads_created_time():
    raw = {"record_id": "rec1", "fields": {"姓名": "张三"}, "created_time": 1757433600000}
    record = S.normalize_record(raw)

    assert record["record_id"] == "rec1"
    assert record["created_time_ms"] == 1757433600000
    assert record["fields"] == {"姓名": "张三"}
