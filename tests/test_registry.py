"""M2 红-绿切片：活动注册表。

覆盖：动态发现、部组来自文件夹层级、排除自关联、学时字段命名差异、
非活动表剔除、单表失败隔离。
"""

from __future__ import annotations

from config import BaseSpec, clean_activity_name, normalize_term, term_sort_value
from feishu.registry import (
    build_main_link_index,
    build_registry,
    folder_path_of,
    resolve_group,
    resolve_term,
)
from tests.conftest import (
    BASE_25_26,
    BASE_26_27,
    MAIN_25_26,
    MAIN_26_27,
    build_fixture_client,
)


# --------------------------------------------------------------------------
# 名称清洗
# --------------------------------------------------------------------------


def test_clean_activity_name_strips_questionnaire_suffix():
    assert clean_activity_name("2025秋金盲杖反馈、学时问卷") == "2025秋金盲杖"
    assert clean_activity_name("26秋-安宁疗护-反馈问卷") == "26秋-安宁疗护"
    assert (
        clean_activity_name("2026春活动名反馈、学时问卷（模板勿用） 副本 2")
        == "2026春活动名"
    )
    assert clean_activity_name("9月20日同心圆游园会") == "9月20日同心圆游园会"
    assert clean_activity_name("2025秋敬老院活动反馈、学时问卷") == "2025秋敬老院活动"


# --------------------------------------------------------------------------
# 学期归一
# --------------------------------------------------------------------------


def test_normalize_term_handles_two_and_four_digit_years():
    assert normalize_term("2025秋") == "2025秋"
    assert normalize_term("25秋") == "2025秋"
    assert normalize_term("26秋") == "2026秋"
    assert normalize_term("2026春") == "2026春"
    assert normalize_term("与爱心社无关") is None


def test_term_sort_value_orders_spring_before_fall():
    assert term_sort_value("2025春") < term_sort_value("2025秋")
    assert term_sort_value("2025秋") < term_sort_value("2026春")
    assert term_sort_value("垃圾") is None


# --------------------------------------------------------------------------
# 文件夹 → 部组
# --------------------------------------------------------------------------


def test_resolve_group_prefers_topmost_known_department():
    assert resolve_group(["资助部", "2025秋"]) == "资助部"
    assert resolve_group(["无障碍部", "25秋"]) == "无障碍部"
    assert resolve_group(["护老部"]) == "护老部"


def test_resolve_group_substring_matches_renamed_folders():
    assert resolve_group(["组织部", "组织部25秋"]) == "组织部"


def test_resolve_group_rejects_unrecognised_folder():
    """不是部组的文件夹名不许冒充部组。

    「1+n第二期」是专项工作流的文件夹，之前会被当成部组，与 9 个真部组
    并排出现在「参与浓度」里 —— 那不是部组，不该占一个标签位。
    """
    assert resolve_group(["1+n第二期"]) is None
    assert resolve_group(["2025秋活动记录"]) is None
    assert resolve_group([]) is None


def test_folder_path_of_walks_up_to_root(fixture_client):
    blocks = fixture_client.blocks[BASE_26_27]
    index = {b["id"]: b for b in blocks}
    assert folder_path_of("bfl5FRqq2Cqlq3lv", index) == ["护老部"]
    assert folder_path_of(None, index) == []

    blocks_25 = fixture_client.blocks[BASE_25_26]
    index_25 = {b["id"]: b for b in blocks_25}
    assert folder_path_of("bflY6yIgcLIFw6LY", index_25) == ["资助部", "2025秋"]


def test_resolve_term_prefers_deepest_folder_then_table_name():
    assert resolve_term(["资助部", "2025秋"], "2025秋资助部日常工作") == "2025秋"
    assert resolve_term(["组织部", "组织部25秋"], "2025秋扫雪") == "2025秋"
    assert resolve_term([], "26秋-护老周-反馈问卷") == "2026秋"


# --------------------------------------------------------------------------
# 主表 link 索引
# --------------------------------------------------------------------------


def test_main_link_index_excludes_self_reference():
    """26-27 主表有指向自己的「父记录」字段，绝不能被当活动。"""
    from feishu import schema as S

    fields = S.normalize_fields(
        [
            {
                "field_id": "f1",
                "field_name": "父记录",
                "type": 18,
                "property": {"table_id": MAIN_26_27},
            },
            {
                "field_id": "f2",
                "field_name": "26秋-安宁疗护-反馈问卷",
                "type": 21,
                "property": {"table_id": "tblp8GDapgvtG4wP"},
            },
        ]
    )
    index = build_main_link_index(fields, MAIN_26_27)

    assert index == {"tblp8GDapgvtG4wP": "26秋-安宁疗护-反馈问卷"}


# --------------------------------------------------------------------------
# 注册表构建
# --------------------------------------------------------------------------


def test_registry_discovers_only_tables_linking_back_to_main(specs):
    client = build_fixture_client()
    registry = build_registry(client, specs)
    ids = {t.table_id for t in registry.tables}

    # 有指回主表的 link 字段 → 是活动表
    assert "tblp8GDapgvtG4wP" in ids
    assert "tblB74WxX7708aKd" in ids

    # 主表自己、意见反馈渠道、模板表、编号表都不算活动
    assert MAIN_26_27 not in ids
    assert MAIN_25_26 not in ids
    assert "tbl4GS1kFZLpv6t6" not in ids
    assert "tblI5dedmB7cuNJy" not in ids
    assert "tbleCOz8lHcOHXvs" not in ids

    # 没有指回主表的表不是活动表
    assert "tblD1VlzTQSlPDM2" not in ids


def test_registry_group_comes_from_folder_not_questionnaire(specs):
    """部组必须来自文件夹层级，不是入社问卷里填的「感兴趣的部组」。"""
    client = build_fixture_client()
    registry = build_registry(client, specs)
    by_id = registry.by_table_id()

    assert by_id["tblp8GDapgvtG4wP"].group == "护老部"
    assert by_id["tblNVIQJOZZHCaC8"].group == "护老部"
    assert by_id["tbl6CT22uQUGe7gV"].group == "无障碍部"
    assert by_id["tblcaPosQ1bDsFbE"].group == "儿童部"
    assert by_id["tblB74WxX7708aKd"].group == "无障碍部"
    assert by_id["tblzVoQoJQUAbjLi"].group == "组织部"
    # 「1+n第二期」是专项工作流的文件夹，不是部组 —— 不该占一个部组标签位
    assert by_id["tblhnwSMlvHsm5RE"].group is None


def test_registry_term_is_resolved(specs):
    client = build_fixture_client()
    registry = build_registry(client, specs)
    by_id = registry.by_table_id()

    assert by_id["tblp8GDapgvtG4wP"].term == "2026秋"
    assert by_id["tblB74WxX7708aKd"].term == "2025秋"
    assert by_id["tbl2vLt6URHr731c"].term == "2025秋"


def test_registry_detects_hours_field_naming_variants(specs):
    """志愿时长 / 志愿学时 两种命名都要认出来。"""
    client = build_fixture_client()
    registry = build_registry(client, specs)
    by_id = registry.by_table_id()

    assert by_id["tblp8GDapgvtG4wP"].hours_fields == ("志愿时长",)
    assert by_id["tblB74WxX7708aKd"].hours_fields == ("志愿学时",)
    assert by_id["tbl2vLt6URHr731c"].hours_fields == ("志愿学时",)


def test_registry_detects_time_and_identity_fields(specs):
    client = build_fixture_client()
    registry = build_registry(client, specs)
    by_id = registry.by_table_id()

    auhen = by_id["tblp8GDapgvtG4wP"]
    assert auhen.time_fields[0] == "参与时间"
    assert auhen.has_name is True
    assert auhen.has_sid is False

    jinmang = by_id["tblB74WxX7708aKd"]
    assert jinmang.has_sid is True

    daily = by_id["tbl3JwOI6k9XBFc1"]
    assert daily.time_fields[0] == "活动时间", "强提示名称应排前"


def test_registry_records_main_link_field_and_activity_name(specs):
    client = build_fixture_client()
    registry = build_registry(client, specs)
    by_id = registry.by_table_id()

    auhen = by_id["tblp8GDapgvtG4wP"]
    assert auhen.main_link_field == "26秋-安宁疗护-反馈问卷"
    assert auhen.name == "26秋-安宁疗护"
    assert auhen.link_field == "社员档案"
    assert auhen.has_main_link is True


def test_registry_skips_empty_tables_metadata(specs):
    client = build_fixture_client()
    registry = build_registry(client, specs)
    by_id = registry.by_table_id()

    assert by_id["tblp8GDapgvtG4wP"].record_count_hint == 10
    assert by_id["tbl3JwOI6k9XBFc1"].record_count_hint == 0


def test_registry_isolates_single_table_failure(specs):
    """单张表探测失败要被隔离，但**必须留下痕迹**。

    这张表被跳过意味着它的活动对所有用户都不可见，而注册表要缓存 24 小时。
    所以「失败被记录进 registry.errors」不是可选的调试信息，而是
    「结果可能不完整」的唯一信号 —— `query_member` 会把它透出到页面上。
    """
    client = build_fixture_client(fail_tables=["tblFAILcandidate001"])
    registry = build_registry(client, specs)

    ids = {t.table_id for t in registry.tables}
    assert "tblFAILcandidate001" not in ids
    assert "tblp8GDapgvtG4wP" in ids, "单表失败不得影响其他表"

    assert registry.errors, "失败必须被记录 —— 这是「数据可能缺失」的唯一信号"
    assert any("某未开放表" in e for e in registry.errors)


def test_registry_survives_one_base_being_unreadable(specs):
    """某个库整库读不到时，另一个库仍应产出结果。"""

    from tests.conftest import FakeBitableClient, load_fixture

    class HalfBrokenClient(FakeBitableClient):
        def list_blocks(self, base_token, **kwargs):
            if base_token == BASE_25_26:
                raise RuntimeError("模拟 403：未添加协作者")
            return super().list_blocks(base_token, **kwargs)

    raw_fields = load_fixture("fields.json")
    flat: dict[tuple[str, str], list] = {}
    for base, tables in raw_fields.items():
        for tid, items in tables.items():
            flat[(base, tid)] = items

    client = HalfBrokenClient(blocks=load_fixture("blocks.json"), fields=flat)
    registry = build_registry(client, specs)

    ids = {t.table_id for t in registry.tables}
    assert "tblp8GDapgvtG4wP" in ids
    assert "tblB74WxX7708aKd" not in ids
    assert any("25-26" in e for e in registry.errors)


def test_registry_group_aggregation(specs):
    client = build_fixture_client()
    registry = build_registry(client, specs)
    grouped = registry.by_group()

    assert len(grouped["护老部"]) == 3
    assert "无障碍部" in grouped


def test_registry_main_link_index_pairs_field_to_table(specs):
    client = build_fixture_client()
    registry = build_registry(client, specs)
    index = registry.main_link_index("base_26_27")

    assert index["26秋-安宁疗护-反馈问卷"] == "tblp8GDapgvtG4wP"
    assert "父记录" not in index


# --------------------------------------------------------------------------
# 字段探测：真实数据里踩到的坑
# --------------------------------------------------------------------------


def test_detect_time_field_accepts_date_wording():
    """真实活动表里参与时间字段叫「填写日期」，不是「时间」。"""
    from feishu import schema as S
    from feishu.registry import detect_time_fields

    fields = S.normalize_fields(
        [
            {"field_id": "f1", "field_name": "填写日期", "type": 5},
            {"field_id": "f2", "field_name": "参与时间", "type": 5},
            {"field_id": "f3", "field_name": "志愿学时", "type": 2},
            {"field_id": "f4", "field_name": "姓名", "type": 1},
            {"field_id": "f5", "field_name": "签到时刻", "type": 5},
        ]
    )
    times = detect_time_fields(fields)

    assert set(times) == {"填写日期", "参与时间"}, "「日期」口径必须被识别"
    assert times[0] == "参与时间", "强提示名称应排前面"


def test_hours_field_ignores_text_and_formula_columns():
    """「学时录入情况备注」是文本、「志愿学时录入公式」是公式，都不能算学时。"""
    from feishu import schema as S
    from feishu.registry import detect_hours_fields

    fields = S.normalize_fields(
        [
            {"field_id": "f1", "field_name": "志愿学时", "type": 2},
            {"field_id": "f2", "field_name": "学时录入情况备注", "type": 1},
            {"field_id": "f3", "field_name": "志愿学时录入公式", "type": 20},
            {"field_id": "f4", "field_name": "学号", "type": 2},
        ]
    )
    assert detect_hours_fields(fields) == ("志愿学时",)


def test_sensitive_fields_excluded_from_safe_projection(specs):
    """活动表里真的有「身份证号」列，必须被挡在投影之外。"""
    from feishu import schema as S
    from feishu.registry import _probe_one
    from config import SENSITIVE_FIELDS

    spec = next(s for s in specs if s.key == "base_25_26")
    block = {"id": "tblX", "name": "2025秋某活动", "type": "table", "parent_id": None}
    client = build_fixture_client()
    client.fields[(spec.base_token, "tblX")] = [
        {"field_id": "f1", "field_name": "姓名", "type": 1},
        {"field_id": "f2", "field_name": "身份证号", "type": 1},
        {"field_id": "f3", "field_name": "您的微信号", "type": 1},
        {"field_id": "f4", "field_name": "志愿学时", "type": 2},
        {
            "field_id": "f5",
            "field_name": "社员档案",
            "type": 21,
            "property": {"table_id": spec.main_table_id},
        },
    ]

    table = _probe_one(client, spec, block, {"tblX": block}, {})
    assert table is not None
    assert "姓名" in table.safe_fields
    assert "志愿学时" in table.safe_fields
    assert not (set(table.safe_fields) & SENSITIVE_FIELDS)


# --------------------------------------------------------------------------
# 超长字段名：不能进请求投影（真实事故）
# --------------------------------------------------------------------------


PROSE_FIELD = (
    "本学期日常工作的学时将在寒假统一发放，届时会有人联系同学拉群，"
    "需要扫二维码加入志愿北京项目才能记录时长，请务必关注哦！"
)


def test_safe_fields_excludes_over_long_names(specs):
    """⚠️ 超长字段名进了请求投影，飞书会拒掉**整个请求**，整表数据全丢。

    真实触发源：有人把问卷题干当成了列名。fixture 里 26秋-安宁疗护 就带一个。
    """
    client = build_fixture_client()
    registry = build_registry(client, specs)
    table = registry.by_table_id()["tblp8GDapgvtG4wP"]

    assert PROSE_FIELD not in table.safe_fields, "超长字段名不得进入请求投影"
    assert "姓名" in table.safe_fields, "其余字段照常保留"

    # 反过来验证：真的发出去的投影里也不许出现它
    for _op, _tid, names in client.projections:
        assert PROSE_FIELD not in names


def test_hours_detection_skips_over_long_names():
    """万一有人把「学时」写进超长列名，宁可漏掉这个字段，也不能打挂整张表。"""
    from feishu.registry import detect_hours_fields

    fields = [
        {"name": "志愿时长", "type": 2},
        {"name": PROSE_FIELD, "type": 2},  # 含「学时」且是数字字段，但名字太长
    ]
    assert detect_hours_fields(fields) == ("志愿时长",)


def test_time_detection_skips_over_long_names():
    from feishu.registry import detect_time_fields

    fields = [
        {"name": "参与时间", "type": 5},
        {"name": PROSE_FIELD + "的参与时间", "type": 5},
    ]
    assert detect_time_fields(fields) == ("参与时间",)
