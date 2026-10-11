"""UI 层：自绘 HTML 组件。

全部是「数据进、HTML 字符串出」的纯函数，方便预览与复用。
所有来自飞书的数据都先经过 ``html.escape``，避免注入。
"""

from __future__ import annotations

from html import escape
from typing import Iterable, Sequence

from config import (
    BRAND_CRIMSON,
    GROUP_FALLBACK_COLOR,
    ORG_NAME,
    PALETTE_MIST,
    UNKNOWN_GROUP_LABEL,
    group_color,
)
from feishu.models import Footprint, TimelineItem
from services.membership_date import format_date
from ui import theme

# --------------------------------------------------------------------------
# 基础
# --------------------------------------------------------------------------


def esc(value: object) -> str:
    return escape("" if value is None else str(value))


def _badge(text: str, kind: str) -> str:
    return f'<span class="ax-badge ax-badge-{kind}">{esc(text)}</span>'


def note(kind: str, body: str, *, html: bool = False) -> str:
    """提示条。

    默认**转义** ``body``；只有调用方明确传 ``html=True`` 时才当作富文本渲染。
    这样可以防止「顺手把用户数据塞进来」造成的注入。
    """
    icon = {
        "info": "💡",
        "warn": "⚠️",
        "error": "🚫",
        "empty": "🌱",
    }.get(kind, "💡")
    inner = body if html else esc(body)
    return (
        f'<div class="ax-note ax-note-{kind}"><span>{icon}</span>'
        f"<div>{inner}</div></div>"
    )


def section(title: str, note_text: str = "") -> str:
    tail = (
        f'<span class="ax-section-note">{esc(note_text)}</span>' if note_text else ""
    )
    return f'<div class="ax-section">{esc(title)}{tail}</div>'


# --------------------------------------------------------------------------
# 顶部品牌区
# --------------------------------------------------------------------------


def hero(title: str, subtitle: str) -> str:
    """顶部品牌区：官方栏图 + 深红标题带（与海报同一套版式）。"""
    banner = theme.banner_uri()
    if banner:
        media = f'<img class="ax-hero-banner" src="{banner}" alt="{esc(ORG_NAME)}"/>'
    else:  # 素材缺失时退回纯色块，不留白洞
        media = ""
    return f"""
<div class="ax-hero">
  {media}
  <div class="ax-hero-band">
    <div class="ax-hero-title">{esc(title)}</div>
    <p class="ax-hero-sub">{esc(subtitle)}</p>
  </div>
</div>
"""


# --------------------------------------------------------------------------
# 结果页头
# --------------------------------------------------------------------------


def result_header(
    name: str,
    *,
    join_label: str,
    join_badge: str,
    days: int | None,
    base_labels: Sequence[str],
    duplicate: bool,
) -> str:
    meta_bits: list[str] = []
    if join_label:
        meta_bits.append(f"入社时间 <b>{esc(join_label)}</b> {join_badge}")
    if days is not None:
        meta_bits.append(f"已陪伴爱心社 <b>{days}</b> 天")
    if base_labels:
        meta_bits.append("数据来自 " + "、".join(esc(b) for b in base_labels))
    if duplicate:
        meta_bits.append('<span class="ax-badge ax-badge-est">档案有重复记录</span>')

    return f"""
<div class="ax-result-hero">
  <div class="ax-result-name">{esc(name)} 的爱心足迹</div>
  <div class="ax-result-meta">{'　·　'.join(meta_bits)}</div>
</div>
"""


def join_badge(is_estimated: bool) -> str:
    if is_estimated:
        return _badge("约为首次入档时间", "est")
    return _badge("真实填写", "real")


# --------------------------------------------------------------------------
# 统计卡
# --------------------------------------------------------------------------


def stats(items: Sequence[tuple[str, str, str]]) -> str:
    """items: [(标签, 数值文本, 单位)]"""
    cards = []
    for index, (label, value, unit) in enumerate(items):
        cards.append(
            f"""
  <div class="ax-stat" style="--i:{index}">
    <div class="ax-stat-label">{esc(label)}</div>
    <div class="ax-stat-value">{esc(value)}<span class="ax-stat-unit">{esc(unit)}</span></div>
  </div>"""
        )
    return '<div class="ax-stats">' + "".join(cards) + "</div>"


def stat_items(footprint: Footprint) -> list[tuple[str, str, str]]:
    hours = (
        str(int(footprint.total_hours))
        if float(footprint.total_hours).is_integer()
        else f"{footprint.total_hours:.1f}"
    )
    return [
        ("累计志愿学时", hours, "小时"),
        ("参与活动", str(footprint.distinct_activity_count), "场"),
        ("足迹部组", str(len(footprint.groups)), "个"),
    ]


# --------------------------------------------------------------------------
# 部组色标
# --------------------------------------------------------------------------


def group_pills(groups: Iterable[str | None]) -> str:
    chips = []
    for group in groups:
        label = group or UNKNOWN_GROUP_LABEL
        color = group_color(group) or GROUP_FALLBACK_COLOR
        chips.append(
            f'<span class="ax-pill" style="background:{color}55">'
            f'<span class="ax-pill-dot" style="background:{color}"></span>'
            f"{esc(label)}</span>"
        )
    if not chips:
        return ""
    return '<div class="ax-pills">' + "".join(chips) + "</div>"


# --------------------------------------------------------------------------
# 时间轴
# --------------------------------------------------------------------------


def _group_chip(group: str | None) -> str:
    label = group or UNKNOWN_GROUP_LABEL
    color = group_color(group)
    return (
        f'<span class="ax-pill" style="background:{color}33;padding:2px 9px;font-size:11.5px">'
        f'<span class="ax-pill-dot" style="background:{color}"></span>{esc(label)}</span>'
    )


def timeline(items: Sequence[TimelineItem]) -> str:
    if not items:
        return note(
            "empty",
            "还没有查到活动记录。<br/>如果你确实参加过活动，可能是姓名或学号填写有出入。",
            html=True,
        )

    rows = []
    for index, item in enumerate(items):
        color = group_color(item.group)
        when = format_date(item.event_time_ms) or (item.term or "时间待补")
        hours = (
            f'<span class="ax-tl-hours">'
            f'{"+" if item.hours else ""}{_fmt_hours(item.hours)}h</span>'
            if item.hours
            else ""
        )
        low = (
            _badge("待确认", "low")
            if item.confidence == "low"
            else ""
        )
        reflection = (
            f'<div class="ax-tl-note">{esc(item.reflection)}</div>'
            if item.reflection
            else ""
        )
        rows.append(
            f"""
<div class="ax-tl-item" style="--i:{index}">
  <span class="ax-tl-dot" style="background:{color}"></span>
  <div class="ax-tl-card">
    <div class="ax-tl-head">
      <span class="ax-tl-name">{esc(item.activity_name)}</span>
      {hours}
    </div>
    <div class="ax-tl-meta">
      {_group_chip(item.group)}
      <span>{esc(when)}</span>
      {low}
    </div>
    {reflection}
  </div>
</div>"""
        )
    return '<div class="ax-timeline">' + "".join(rows) + "</div>"


def _fmt_hours(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}"


def term_chips(counts: dict[str, int]) -> str:
    """学期分布小标签。"""
    if not counts:
        return ""
    chips = []
    for term, count in counts.items():
        chips.append(
            f'<span class="ax-pill" style="background:{PALETTE_MIST}66">{esc(term)}'
            f'<b style="color:{BRAND_CRIMSON};margin-left:6px">{count}</b></span>'
        )
    return '<div class="ax-pills">' + "".join(chips) + "</div>"


def metric_row(items: Sequence[tuple[str, str]]) -> str:
    """一行「标签：值」的文字指标。"""
    parts = [
        f'<span style="margin-right:20px">{esc(label)} '
        f'<b style="color:{BRAND_CRIMSON}">{esc(value)}</b></span>'
        for label, value in items
    ]
    return f'<div class="ax-result-meta">{"".join(parts)}</div>'


# --------------------------------------------------------------------------
# 页脚
# --------------------------------------------------------------------------


def footnote(lines: Sequence[str]) -> str:
    body = "<br/>".join(esc(line) for line in lines if line)
    return f'<div class="ax-footnote">{body}</div>'
