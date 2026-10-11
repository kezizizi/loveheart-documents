"""M6 切片：分享海报渲染。"""

from __future__ import annotations

import io
from datetime import date

from PIL import Image

from feishu.models import Footprint, MembershipDate, TimelineItem
from services.poster import (
    POSTER_HEIGHT,
    POSTER_WIDTH,
    poster_file_name,
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


def test_poster_is_valid_png_with_expected_size():
    data = render_poster(
        name="张三",
        footprint=_sample_footprint(),
        membership=MembershipDate(
            date_ms=1_696_032_000_000, source="入社日期字段", is_estimated=False
        ),
        data_as_of=date(2026, 10, 11),
    )

    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(data) > 10_000

    with _open(data) as img:
        assert img.size == (POSTER_WIDTH, POSTER_HEIGHT)


def test_poster_renders_with_empty_footprint():
    data = render_poster(
        name="新社员",
        footprint=Footprint(
            total_hours=0.0,
            activity_count=0,
            distinct_activity_count=0,
            groups=(),
            timeline=(),
        ),
        membership=MembershipDate(date_ms=None, source="未记录", is_estimated=True),
    )
    with _open(data) as img:
        assert img.size == (POSTER_WIDTH, POSTER_HEIGHT)


def test_poster_survives_long_names_and_many_items():
    data = render_poster(
        name="欧阳娜娜娜娜娜娜娜娜娜娜",
        footprint=_sample_footprint(n_items=25, long_name=True),
        membership=MembershipDate(
            date_ms=1_700_000_000_000, source="估算", is_estimated=True
        ),
        subtitle="23秋以前入社",
    )
    with _open(data) as img:
        assert img.size == (POSTER_WIDTH, POSTER_HEIGHT)


def test_poster_file_name_is_sanitized():
    assert poster_file_name("张三", date(2026, 10, 11)) == "张三_爱心足迹_20261011.png"
    assert poster_file_name("a/b", date(2026, 1, 2)) == "a_b_爱心足迹_20260102.png"
