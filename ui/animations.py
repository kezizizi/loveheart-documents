"""UI 层：动效封装。

Streamlit 的能力边界（诚实说明）：
* **可行**：纯 CSS ``@keyframes``（心跳 loading、卡片入场延误），
  以及「Python 循环 + 逐个 delta 推送」实现的数字滚动。
* **做不到**：主 DOM 内的 ``<script>`` 会被 Streamlit 剥离，因此
  高频 JS 计数、滚动联动、跨 rerun 的持续动画都实现不了。

动效因此全部集中在「点查询 → 出结果」这一帧，且都是**一次性**的。
"""

from __future__ import annotations

import time
from typing import Sequence

import streamlit as st

from ui import components, theme

DEFAULT_LOADING_TEXT = "小爱同学正在回顾您的爱心足迹…"


def heartbeat(text: str = DEFAULT_LOADING_TEXT):
    """爱心跳动的 loading 占位块，返回占位对象，用完请 ``.empty()``。"""
    placeholder = st.empty()
    uri = theme.logo_uri()
    logo = f'<img src="{uri}" alt="爱心社社徽"/>' if uri else "<span>❤</span>"
    placeholder.html(
        f"""
<div class="ax-loading">
  <div class="ax-loading-heart">{logo}</div>
  <div class="ax-loading-trail"><span></span><span></span><span></span></div>
  <div class="ax-loading-text">{components.esc(text)}</div>
</div>
"""
    )
    return placeholder


def count_up_stats(
    placeholder,
    items: Sequence[tuple[str, str, str]],
    *,
    frames: int = 14,
    delay: float = 0.04,
) -> None:
    """统计卡数字滚动增长。

    只有纯数字的值会滚动；非数字（例如「—」）原样显示。
    最后必然停在精确值上，不会出现四舍五入误差。
    """
    targets: list[float | None] = []
    for _label, value, _unit in items:
        try:
            targets.append(float(value))
        except (TypeError, ValueError):
            targets.append(None)

    for frame in range(1, frames + 1):
        progress = 1 - (1 - frame / frames) ** 3  # ease-out cubic
        shown_rows: list[tuple[str, str, str]] = []
        for (label, value, unit), target in zip(items, targets):
            if target is None:
                shown = value
            elif float(target).is_integer():
                shown = str(int(round(target * progress)))
            else:
                shown = f"{target * progress:.1f}"
            shown_rows.append((label, shown, unit))
        placeholder.html(components.stats(shown_rows))
        if frame < frames:
            time.sleep(delay)
