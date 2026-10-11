"""M6 切片：分享海报渲染。"""

from __future__ import annotations

import io
from datetime import date

from PIL import Image

from feishu.models import Footprint, MembershipDate, TimelineItem
from services.poster import (
    POSTER_MIN_HEIGHT,
    POSTER_WIDTH,
    TIMELINE_ROW_H,
    poster_file_name,
    poster_height,
    render_poster,
)


def _sample_footprint(n_items: int = 4, long_name: bool = False) -> Footprint:
    timeline = []
    for i in range(n_items):
        title = (
            "2025秋国庆“金秋献礼，花香暖桑榆”插花活动特别场次加长名称测试"
            if long_name
            else f"活动{i}"
        )
        timeline.append(
            TimelineItem(
                activity_name=title,
                group=["护老部", "无障碍部", "手语分社", None][i % 4],
                term=["2025秋", "2026春", None][i % 3],
                event_time_ms=1_700_000_000_000 + i * 86_400_000,
                hours=1.5 * i,
            )
        )
    return Footprint(
        total_hours=12.0,
        activity_count=n_items,
        distinct_activity_count=max(1, n_items - 1),
        groups=("护老部", "无障碍部", "手语分社", "其他"),
        timeline=tuple(timeline),
        hour_by_group={"护老部": 6.0, "无障碍部": 4.0},
        count_by_term={"2025秋": 2, "2026春": 2},
    )


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def _render(footprint: Footprint, name: str = "张三") -> Image.Image:
    data = render_poster(
        name=name,
        footprint=footprint,
        membership=MembershipDate(
            date_ms=1_696_032_000_000, source="入社日期字段", is_estimated=False
        ),
        data_as_of=date(2026, 10, 11),
    )
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return _open(data)


# --------------------------------------------------------------------------
# 画幅：随活动数生长，但绝不缩水、绝不截断
# --------------------------------------------------------------------------


def test_short_poster_keeps_minimum_height():
    """活动少的社员，画幅不小于基准尺寸 —— 版式不缩水。"""
    assert poster_height(_sample_footprint(n_items=3)) == POSTER_MIN_HEIGHT
    assert poster_height(_sample_footprint(n_items=0)) == POSTER_MIN_HEIGHT


def test_poster_height_grows_so_every_activity_fits():
    """活动多的社员，画幅必须变长，把每一条都画下。"""
    few = poster_height(_sample_footprint(n_items=3))
    many = poster_height(_sample_footprint(n_items=40))

    assert many > POSTER_MIN_HEIGHT
    assert many > few


def test_height_is_linear_in_activity_count():
    """每多一条活动就多一行 —— 证明是「全都画下」，而不是挑着画。

    如果还留着「画不下就截断」的逻辑，这个等式就不成立。
    """
    ten = poster_height(_sample_footprint(n_items=10))
    twenty = poster_height(_sample_footprint(n_items=20))

    assert twenty - ten == 10 * TIMELINE_ROW_H


def test_rendered_height_matches_computed_height():
    for n_items in (3, 12, 40):
        footprint = _sample_footprint(n_items=n_items)
        with _render(footprint) as img:
            assert img.size == (POSTER_WIDTH, poster_height(footprint))


def test_poster_renders_with_empty_footprint():
    footprint = Footprint(
        total_hours=0.0,
        activity_count=0,
        distinct_activity_count=0,
        groups=(),
        timeline=(),
    )
    with _render(footprint, name="新社员") as img:
        assert img.size == (POSTER_WIDTH, POSTER_MIN_HEIGHT)


def test_poster_survives_long_names_and_many_items():
    footprint = _sample_footprint(n_items=25, long_name=True)
    with _render(footprint, name="欧阳娜娜娜娜娜娜娜娜娜娜") as img:
        assert img.size == (POSTER_WIDTH, poster_height(footprint))


def test_poster_file_name_is_sanitized():
    assert poster_file_name("张三", date(2026, 10, 11)) == "张三_爱心足迹_20261011.png"
    assert poster_file_name("a/b", date(2026, 1, 2)) == "a_b_爱心足迹_20260102.png"
