"""业务层：身份归一化与跨库合并。

**这是整个系统最容易翻车的地方**：25-26 库的「学号」是数字字段，
26-27 库的「学号」是文本字段，不归一化就永远匹配不上。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

from feishu.models import MemberRecord

_WS_RE = re.compile(r"\s+")


def normalize_sid(raw: Any) -> str:
    """学号归一化成纯数字字符串。

    ``2024001`` / ``"2024001"`` / ``"2024001.0"`` / ``2024001.0`` 全部 → ``"2024001"``。
    """
    if raw is None or isinstance(raw, bool):
        return ""

    if isinstance(raw, (int, float)):
        value = float(raw)
        return str(int(value)) if value.is_integer() else repr(value).rstrip("0").rstrip(".")

    text = str(raw).strip()
    if not text:
        return ""
    text = _WS_RE.sub("", text)
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def normalize_name(raw: Any) -> str:
    """姓名归一化：去掉所有空白，避免「张 三」与「张三」不相等。"""
    if raw is None:
        return ""
    return _WS_RE.sub("", str(raw))


def identity_key(sid: Any, name: Any) -> tuple[str, str]:
    return normalize_sid(sid), normalize_name(name)


def sids_equal(a: Any, b: Any) -> bool:
    left, right = normalize_sid(a), normalize_sid(b)
    return bool(left) and left == right


def names_equal(a: Any, b: Any) -> bool:
    left, right = normalize_name(a), normalize_name(b)
    return bool(left) and left == right


@dataclass(frozen=True)
class MergedMember:
    """同一个人在两个库里可能的全部主表记录，合并后的视图。"""

    name: str
    sid: str
    records: tuple[MemberRecord, ...]

    @property
    def base_keys(self) -> tuple[str, ...]:
        seen: list[str] = []
        for r in self.records:
            if r.base_key not in seen:
                seen.append(r.base_key)
        return tuple(seen)

    @property
    def in_multiple_bases(self) -> bool:
        return len(self.base_keys) > 1

    @property
    def has_duplicate_records(self) -> bool:
        """同一个库里出现多条同名同学号记录（一般是重复填写）。"""
        counts: dict[str, int] = {}
        for r in self.records:
            counts[r.base_key] = counts.get(r.base_key, 0) + 1
        return any(c > 1 for c in counts.values())

    def _ordered(self) -> list[MemberRecord]:
        """按记录创建时间升序；无时间戳的排最后。"""

        def key(rec: MemberRecord) -> tuple[int, int]:
            ts = rec.created_time_ms
            return (0, ts) if ts else (1, 0)

        return sorted(self.records, key=key)

    def value(self, *field_names: str) -> Any:
        """取字段值：越新的记录优先，空值跳过。"""
        for rec in reversed(self._ordered()):
            for name in field_names:
                v = rec.raw_fields.get(name)
                if v not in (None, "", [], {}):
                    return v
        return None

    def all_record_ids(self) -> tuple[str, ...]:
        return tuple(r.record_id for r in self.records)

    def record_ids_of(self, base_key: str) -> tuple[str, ...]:
        return tuple(r.record_id for r in self.records if r.base_key == base_key)

    def link_targets_of(self, base_key: str, field_name: str) -> tuple[str, ...]:
        out: list[str] = []
        for rec in self.records:
            if rec.base_key != base_key:
                continue
            out.extend(rec.link_targets.get(field_name, ()))
        seen: set[str] = set()
        result: list[str] = []
        for rid in out:
            if rid not in seen:
                seen.add(rid)
                result.append(rid)
        return tuple(result)


def merge_members(records: Sequence[MemberRecord]) -> MergedMember | None:
    """把同一人的全部主表记录合并。

    调用方应保证传入的 records 已经是同一个人（姓名 + 学号 归一后相等）。
    """
    if not records:
        return None

    # 姓名/学号取「最完整」的那个写法作为展示值
    best_name = max((r.name for r in records), key=len, default="")
    best_sid = max((r.sid for r in records), key=len, default="")

    return MergedMember(name=best_name, sid=best_sid, records=tuple(records))
