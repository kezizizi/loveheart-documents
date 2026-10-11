"""数据层的数据模型。

全部为不可变 dataclass，便于缓存与比较。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------
# 活动注册表
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ActivityTable:
    """一张活动问卷表。"""

    base_key: str
    base_token: str
    table_id: str
    name: str
    group: str | None = None
    term: str | None = None
    # 活动表内指向主表的 link 字段名（通常叫「社员档案」）
    link_field: str | None = None
    # 主表内指向本表的 link 字段名（有则可用「成员侧快路径」）
    main_link_field: str | None = None
    hours_fields: tuple[str, ...] = ()
    time_fields: tuple[str, ...] = ()
    has_sid: bool = False
    has_name: bool = False
    record_count_hint: int | None = None
    # 本表全部**非敏感**字段名。查询时只投影这一批，
    # 敏感字段（身份证 / 志愿者编号 / 微信号）从注册表阶段就被剔除。
    safe_fields: tuple[str, ...] = ()

    @property
    def has_main_link(self) -> bool:
        return bool(self.main_link_field)

    @property
    def name_field(self) -> str | None:
        """本表里「姓名」那一列**实际**叫什么。

        字段名并不统一（实测有表叫「1、您的姓名」），所以不能硬写「姓名」——
        请求一个不存在的字段名会让整张表的查询直接失败。
        """
        if "姓名" in self.safe_fields:
            return "姓名"
        return next((n for n in self.safe_fields if "姓名" in n), None)

    @property
    def color_key(self) -> str:
        return self.group or "其他"


@dataclass(frozen=True)
class ActivityRegistry:
    """某个时刻的全量活动表快照。"""

    tables: tuple[ActivityTable, ...]
    built_at: float
    errors: tuple[str, ...] = ()
    # base_key → 主表字段名列表（用于构建最小字段投影）
    main_fields: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def main_field_names(self, base_key: str) -> tuple[str, ...]:
        for key, names in self.main_fields:
            if key == base_key:
                return names
        return ()

    def by_table_id(self) -> dict[str, ActivityTable]:
        return {t.table_id: t for t in self.tables}

    def tables_of(self, base_key: str) -> list[ActivityTable]:
        return [t for t in self.tables if t.base_key == base_key]

    def by_group(self) -> dict[str, list[ActivityTable]]:
        out: dict[str, list[ActivityTable]] = {}
        for t in self.tables:
            out.setdefault(t.color_key, []).append(t)
        return out

    def main_link_index(self, base_key: str) -> dict[str, str]:
        """主表 link 字段名 → 活动表 table_id（仅限该库）。"""
        return {
            t.main_link_field: t.table_id
            for t in self.tables
            if t.base_key == base_key and t.main_link_field
        }

    def get(self, table_id: str) -> ActivityTable | None:
        return self.by_table_id().get(table_id)


# --------------------------------------------------------------------------
# 成员
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MemberRecord:
    """主表里的一条成员记录（已做字段白名单投影）。"""

    base_key: str
    base_token: str
    table_id: str
    record_id: str
    name: str
    sid: str
    raw_fields: dict[str, Any] = field(default_factory=dict)
    created_time_ms: int | None = None
    # 主表 link 字段名 → 该字段指向的目标 record_id 列表
    link_targets: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def field(self, *names: str) -> Any:
        """按名称取字段值，未命中返回 None。"""
        for n in names:
            if n in self.raw_fields:
                return self.raw_fields[n]
        return None


@dataclass(frozen=True)
class Participation:
    """一次活动参与记录。"""

    base_key: str
    table_id: str
    activity_name: str
    group: str | None
    term: str | None
    record_id: str
    event_time_ms: int | None = None
    record_created_ms: int | None = None
    hours: float = 0.0
    hours_raw: str = ""
    reflection: str = ""
    content: str = ""
    # high = link record_id 命中；medium = 学号命中；low = 仅姓名命中
    confidence: str = "high"

    @property
    def color_key(self) -> str:
        return self.group or "其他"


# --------------------------------------------------------------------------
# 汇总结果
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MembershipDate:
    date_ms: int | None
    source: str
    is_estimated: bool


@dataclass(frozen=True)
class TimelineItem:
    activity_name: str
    group: str | None
    term: str | None
    event_time_ms: int | None
    hours: float
    reflection: str = ""
    confidence: str = "high"


@dataclass(frozen=True)
class Footprint:
    total_hours: float
    # 参与次数（一条活动记录算一次）
    activity_count: int
    # 去重后的活动场次数（同一活动多条记录算一场）
    distinct_activity_count: int
    groups: tuple[str, ...]
    timeline: tuple[TimelineItem, ...]
    hour_by_group: dict[str, float] = field(default_factory=dict)
    count_by_term: dict[str, int] = field(default_factory=dict)
    confidence_low: int = 0
    co_partners: int | None = None
    co_partners_truncated: bool = False
