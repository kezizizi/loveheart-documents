"""业务层：入社日期口径。

两个库的字段情况不一样：
* 25-26 库有 datetime 字段「入社日期」（默认值＝记录创建时间）
* 26-27 库**没有日期字段**，只有下拉选项「入社时间」（23秋以前 / 23秋 / …）

因此口径定为：同一个人在两库的全部记录中取最早时间点，
优先采信「入社日期」字段的真实值；该字段为空时回退到记录创建时间
（即首次填写招新问卷的时间），并标记为「估算」。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from config import BEFORE_ALL_TERMS
from feishu import schema as S
from feishu.models import MembershipDate
from services.identity import MergedMember

JOIN_DATE_FIELDS = ("入社日期", "入社时间")
TERM_HINT_FIELDS = ("入社时间",)

# 时区固定为北京时间，避免服务器时区不同导致日期漂移
SHANGHAI_OFFSET = timedelta(hours=8)


def _to_local_datetime(ms: int) -> datetime:
    """毫秒时间戳 → 北京时间（naive，便于与 datetime.now() 直接相减）。"""
    utc = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).replace(tzinfo=None)
    return utc + SHANGHAI_OFFSET


def format_date(ms: int | None, fmt: str = "%Y.%m.%d") -> str:
    if not ms:
        return ""
    return _to_local_datetime(ms).strftime(fmt)


def days_since(ms: int | None, *, now: datetime | None = None) -> int | None:
    """入社至今的天数。按**日期**相减，避免时刻差导致少算一天。"""
    if not ms:
        return None
    today = now or datetime.now()
    return (today.date() - _to_local_datetime(ms).date()).days


def derive_membership_date(member: MergedMember) -> MembershipDate:
    """推导入社日期。"""
    real_dates: list[int] = []
    for record in member.records:
        ts = S.cell_datetime_ms(record.raw_fields.get("入社日期"))
        if ts:
            real_dates.append(ts)

    if real_dates:
        return MembershipDate(
            date_ms=min(real_dates), source="入社日期字段", is_estimated=False
        )

    created = [r.created_time_ms for r in member.records if r.created_time_ms]
    if created:
        return MembershipDate(
            date_ms=min(created),
            source="首次入档时间（≈ 首次填写招新问卷）",
            is_estimated=True,
        )

    return MembershipDate(date_ms=None, source="未记录", is_estimated=True)


def join_term_hint(member: MergedMember) -> str:
    """取「入社时间」下拉选项的原始文本，作为入社年月的补充说明。"""
    for record in member.records:
        for field_name in TERM_HINT_FIELDS:
            text = S.cell_text(record.raw_fields.get(field_name))
            if text:
                return text
    return ""


def join_term_is_early(member: MergedMember) -> bool:
    """入社时间是否为「23秋以前」这类模糊口径。"""
    hint = join_term_hint(member)
    if not hint:
        return False
    return hint.endswith("以前") or hint in BEFORE_ALL_TERMS
