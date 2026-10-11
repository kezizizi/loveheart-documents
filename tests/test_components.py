"""M5 切片：UI 组件的 HTML 生成与转义安全。

UI 层不做浏览器级测试（Streamlit 的渲染无法在单测里断言），
但**纯函数部分**可以：HTML 字符串生成、转义、以及 widget key 的唯一性。
"""

from __future__ import annotations

import ast
from pathlib import Path

import config
from feishu.member_query import (
    NOT_FOUND_NAME,
    NOT_FOUND_SID,
    NOT_FOUND_SID_MISSING,
)
from feishu.models import Footprint, TimelineItem
from ui import components

PAGES = Path(__file__).resolve().parent.parent / "ui" / "pages.py"


# --------------------------------------------------------------------------
# 转义安全
# --------------------------------------------------------------------------


def test_esc_escapes_html():
    assert components.esc("<script>") == "&lt;script&gt;"
    assert components.esc('a"b') == "a&quot;b"
    assert components.esc(None) == ""


def test_note_escapes_by_default():
    """默认必须转义——防止调用方顺手把用户数据塞进来造成注入。"""
    html = components.note("warn", "<img src=x onerror=alert(1)>")
    assert "<img" not in html
    assert "&lt;img" in html


def test_note_opts_in_to_html_explicitly():
    html = components.note("info", "点<a href='#'>这里</a>", html=True)
    assert "<a href='#'>这里</a>" in html


def test_timeline_escapes_activity_name_and_reflection():
    items = [
        TimelineItem(
            activity_name="<b>注入</b>",
            group="护老部",
            term="2025秋",
            event_time_ms=1_700_000_000_000,
            hours=2.0,
            reflection='感想 <script>alert("x")</script>',
        )
    ]
    html = components.timeline(items)

    assert "<b>注入</b>" not in html
    assert "&lt;b&gt;注入&lt;/b&gt;" in html
    assert "<script>" not in html


def test_result_header_escapes_name():
    html = components.result_header(
        "<em>张三</em>",
        join_label="2023.09.30",
        join_badge=components.join_badge(False),
        days=1107,
        base_labels=["25-26 学年"],
        duplicate=False,
    )
    assert "<em>张三</em>" not in html
    assert "&lt;em&gt;" in html


# --------------------------------------------------------------------------
# 结构正确性
# --------------------------------------------------------------------------


def test_hero_uses_banner_and_escapes_title():
    html = components.hero("<b>爱心足迹</b>", "副标题")

    assert "ax-hero-banner" in html, "顶部品牌区应当用官方栏图"
    assert "ax-hero-band" in html, "栏图下方应当有深红标题带"
    assert "<b>爱心足迹</b>" not in html
    assert "&lt;b&gt;爱心足迹&lt;/b&gt;" in html


def test_hero_includes_org_name_in_banner_alt():
    html = components.hero("爱心足迹", "副标题")
    assert "北京大学学生爱心社" in html


def test_group_pills_uses_fallback_color_for_unknown_group():
    html = components.group_pills(["没听说过的部", None])
    assert "没听说过的部" in html
    assert "其他" in html


def test_stats_renders_all_items():
    html = components.stats([("累计志愿学时", "32", "小时"), ("参与活动", "7", "场")])
    assert html.count('class="ax-stat"') == 2
    assert "32" in html and "小时" in html


def test_timeline_empty_state_is_friendly():
    html = components.timeline([])
    assert "还没有查到活动记录" in html


def test_timeline_marks_low_confidence():
    html = components.timeline(
        [
            TimelineItem(
                activity_name="A",
                group=None,
                term=None,
                event_time_ms=None,
                hours=0.0,
                confidence="low",
            )
        ]
    )
    assert "待确认" in html


def test_stat_items_formats_hours():
    footprint = Footprint(
        total_hours=32.0,
        activity_count=8,
        distinct_activity_count=7,
        groups=("护老部",),
        timeline=(),
    )
    items = components.stat_items(footprint)
    assert items[0] == ("累计志愿学时", "32", "小时")
    assert items[1] == ("参与活动", "7", "场")
    assert items[2] == ("足迹部组", "1", "个")


# --------------------------------------------------------------------------
# 回归：widget key 必须唯一
# --------------------------------------------------------------------------


def _collect_widget_keys(path: Path) -> list[str]:
    """静态扫描源码里所有 st.*(key="...") 的 key 字面量。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    keys: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        for kw in node.keywords:
            if kw.arg == "key" and isinstance(kw.value, ast.Constant):
                if isinstance(kw.value.value, str):
                    keys.append(kw.value.value)
    return keys


def test_no_duplicate_streamlit_widget_keys():
    """Streamlit 对重复 key 直接抛异常，且只在运行时才炸——这里静态拦掉。"""
    keys = _collect_widget_keys(PAGES)
    assert keys, "应当扫到 widget key"

    seen: set[str] = set()
    duplicates = {k for k in keys if k in seen or seen.add(k)}  # type: ignore[func-returns-value]
    assert not duplicates, f"存在重复的 widget key：{sorted(duplicates)}"


# --------------------------------------------------------------------------
# 查不到时的引导
# --------------------------------------------------------------------------


def test_supplement_form_url_is_current():
    """补录问卷链接是唯一的自助出口，写错等于把人堵在门外。"""
    assert config.SUPPLEMENT_FORM_URL.endswith("shrcnVbvETHWKfha6F4bP9b2Idb")


def test_not_found_note_always_points_to_supplement_form():
    """不管归因是什么，都必须给出补录入口。"""
    for reason in (NOT_FOUND_NAME, NOT_FOUND_SID, NOT_FOUND_SID_MISSING, None):
        html = components.not_found_note(reason)
        assert config.SUPPLEMENT_FORM_URL in html, f"{reason} 没有给出补录链接"
        assert "补录" in html


def test_not_found_note_explains_missing_sid():
    """档案里没填学号 —— 这是 450 条历史记录的真实归因，提示必须说得具体。"""
    html = components.not_found_note(NOT_FOUND_SID_MISSING)
    assert "没有填学号" in html


def test_not_found_note_asks_to_recheck_sid_when_mismatched():
    html = components.not_found_note(NOT_FOUND_SID)
    assert "学号" in html
    assert "核对" in html


def test_not_found_note_survives_unknown_reason():
    html = components.not_found_note("某个没定义过的原因")
    assert config.SUPPLEMENT_FORM_URL in html
