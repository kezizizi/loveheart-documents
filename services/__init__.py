"""业务层：身份归一、入社日期、学时汇总与聚合。"""

from services.footprint import (
    build_timeline,
    count_co_partners,
    group_breakdown,
    summarize,
    timeline_sort_key,
)
from services.identity import (
    MergedMember,
    identity_key,
    merge_members,
    names_equal,
    normalize_name,
    normalize_sid,
    sids_equal,
)
from services.membership_date import (
    days_since,
    derive_membership_date,
    format_date,
    join_term_hint,
)

__all__ = [
    "MergedMember",
    "build_timeline",
    "count_co_partners",
    "days_since",
    "derive_membership_date",
    "format_date",
    "group_breakdown",
    "identity_key",
    "join_term_hint",
    "merge_members",
    "names_equal",
    "normalize_name",
    "normalize_sid",
    "sids_equal",
    "summarize",
    "timeline_sort_key",
]
