"""单成员跨库查询。

两条通道
--------
* **A 通道（权威、快）**：主表的 link 字段非空 → 直接用 record_id
  批量取活动记录。规避一切重名问题。
* **B 通道（兜底）**：对「主表没有对应 link 字段」的孤儿活动表，
  按姓名关键词扫一遍，再逐条判定。

参与判定优先级：``link record_id`` > ``学号`` > ``姓名``（低置信）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Sequence

from config import MEMBER_READ_WHITELIST
from feishu import schema as S
from feishu.models import (
    ActivityRegistry,
    ActivityTable,
    MemberRecord,
    Participation,
)
from feishu.protocol import BitableClient
from services.identity import (
    MergedMember,
    merge_members,
    names_equal,
    normalize_name,
    normalize_sid,
    sids_equal,
)

# 从活动记录里挑「正文」用的字段关键词
_CONTENT_HINTS = ("核心内容", "活动内容", "环节", "印象深刻", "收获", "学习", "建议")
_REFLECTION_HINTS = ("感想", "想说的话", "收获", "体会")
_MAX_CONTENT_LEN = 160

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"


@dataclass(frozen=True)
class MemberQueryResult:
    found: bool
    member: MergedMember | None
    participations: tuple[Participation, ...]
    errors: tuple[str, ...] = ()
    searched_bases: tuple[str, ...] = ()

    @property
    def low_confidence_count(self) -> int:
        return sum(1 for p in self.participations if p.confidence == CONFIDENCE_LOW)


# --------------------------------------------------------------------------
# 字段投影
# --------------------------------------------------------------------------


def main_projection(registry: ActivityRegistry, spec: Any) -> list[str] | None:
    """主表最小字段投影：白名单 ∩ 实际存在 ∪ 活动 link 字段名。"""
    existing = set(registry.main_field_names(spec.key))
    link_names = {
        t.main_link_field
        for t in registry.tables_of(spec.key)
        if t.main_link_field
    }
    if not existing:
        # 主表字段没读到：退回白名单（不含任何活动 link 名），
        # 至少保证匹配所需的姓名 / 学号在投影里。
        return sorted(set(MEMBER_READ_WHITELIST) | link_names)

    allowed = {n for n in MEMBER_READ_WHITELIST if n in existing}
    return sorted(allowed | link_names)


def _find_key(fields: dict[str, Any], needle: str) -> str | None:
    for key in fields:
        if needle in key:
            return key
    return None


# --------------------------------------------------------------------------
# 找主表记录
# --------------------------------------------------------------------------


def _rows_to_members(
    rows: Sequence[dict[str, Any]],
    spec: Any,
    link_names: set[str],
    name: str,
    sid: str,
) -> list[MemberRecord]:
    out: list[MemberRecord] = []
    for row in rows:
        fields = row.get("fields") or {}
        row_name = S.cell_text(fields.get("姓名")) or S.cell_text(
            fields.get(_find_key(fields, "姓名") or "")
        )
        row_sid = S.cell_text(fields.get("学号")) or S.cell_text(
            fields.get(_find_key(fields, "学号") or "")
        )
        if not names_equal(row_name, name):
            continue
        if not sids_equal(row_sid, sid):
            continue

        link_targets: dict[str, tuple[str, ...]] = {}
        for key, value in fields.items():
            if key in link_names:
                ids = S.cell_link_record_ids(value)
                if ids:
                    link_targets[key] = tuple(ids)

        out.append(
            MemberRecord(
                base_key=spec.key,
                base_token=spec.base_token,
                table_id=spec.main_table_id,
                record_id=row.get("record_id") or "",
                name=normalize_name(row_name),
                sid=normalize_sid(row_sid),
                raw_fields=dict(fields),
                created_time_ms=row.get("created_time_ms"),
                link_targets=link_targets,
            )
        )
    return out


def find_member_records(
    client: BitableClient,
    spec: Any,
    registry: ActivityRegistry,
    name: str,
    sid: str,
) -> list[MemberRecord]:
    """在某一个库里找出该人的主表记录（可能多条）。"""
    projection = main_projection(registry, spec)
    link_names = {
        t.main_link_field
        for t in registry.tables_of(spec.key)
        if t.main_link_field
    }

    attempts: list[tuple[str, list[str]]] = [(name, ["姓名"])]
    # 兜底：万一姓名有异体字 / 关键词语义检索不中，再用学号搜一次
    attempts.append((str(normalize_sid(sid)), ["学号"]))

    for keyword, search_fields in attempts:
        if not keyword:
            continue
        try:
            rows = client.search_records(
                spec.base_token,
                spec.main_table_id,
                field_names=projection,
                keyword=keyword,
                search_fields=search_fields,
                automatic_fields=True,
            )
        except Exception:  # noqa: BLE001 - 单条兜底路径失败不算致命
            continue
        matched = _rows_to_members(rows, spec, link_names, name, sid)
        if matched:
            return matched

    return []


# --------------------------------------------------------------------------
# 采集参与记录
# --------------------------------------------------------------------------


def _pick_hours(table: ActivityTable, fields: dict[str, Any]) -> tuple[float, str]:
    total = 0.0
    raws: list[str] = []
    for field_name in table.hours_fields:
        value = fields.get(field_name)
        number = S.cell_number(value)
        if number is None:
            continue
        total += number
        text = S.cell_text(value)
        if text:
            raws.append(text)
    return total, " + ".join(raws)


def _pick_event_time(table: ActivityTable, fields: dict[str, Any]) -> int | None:
    for field_name in table.time_fields:
        ts = S.cell_datetime_ms(fields.get(field_name))
        if ts:
            return ts
    return None


def _pick_text(
    fields: dict[str, Any],
    hints: Sequence[str],
    *,
    limit: int | None = None,
) -> str:
    for hint in hints:
        key = _find_key(fields, hint)
        if not key:
            continue
        text = S.cell_text(fields.get(key))
        if text:
            return text[:limit] if limit else text
    return ""


def build_participation(
    table: ActivityTable,
    row: dict[str, Any],
    confidence: str,
) -> Participation:
    fields = row.get("fields") or {}
    hours, hours_raw = _pick_hours(table, fields)
    return Participation(
        base_key=table.base_key,
        table_id=table.table_id,
        activity_name=table.name,
        group=table.group,
        term=table.term,
        record_id=row.get("record_id") or "",
        event_time_ms=_pick_event_time(table, fields),
        record_created_ms=row.get("created_time_ms"),
        hours=hours,
        hours_raw=hours_raw,
        reflection=_pick_text(fields, _REFLECTION_HINTS),
        content=_pick_text(fields, _CONTENT_HINTS, limit=_MAX_CONTENT_LEN),
        confidence=confidence,
    )


def judge_confidence(
    table: ActivityTable, row: dict[str, Any], member: MergedMember
) -> str | None:
    """判定这条活动记录是否属于本人，以及可信度。不属于返回 None。"""
    fields = row.get("fields") or {}

    # 1) link record_id —— 最强证据。
    #    若 link 字段有值但不指向本人，说明它明确属于别人，**直接判定不是本人**，
    #    绝不能再退回按姓名匹配（同名不同人就是这么误判出来的）。
    if table.link_field:
        targets = set(S.cell_link_record_ids(fields.get(table.link_field)))
        if targets:
            if targets & set(member.record_ids_of(table.base_key)):
                return CONFIDENCE_HIGH
            return None

    # 2) 学号
    if table.has_sid:
        key = _find_key(fields, "学号")
        if key and sids_equal(fields.get(key), member.sid):
            return CONFIDENCE_MEDIUM

    # 3) 姓名 —— 最弱，仅在没有更强证据时使用
    if table.has_name:
        key = _find_key(fields, "姓名")
        if key and names_equal(S.cell_text(fields.get(key)), member.name):
            return CONFIDENCE_LOW

    return None


def _collect_from_linked(
    client: BitableClient, member: MergedMember, table: ActivityTable
) -> list[Participation]:
    """A 通道：靠主表 link 字段拿到的 record_id 精确取数。"""
    ids = member.link_targets_of(table.base_key, table.main_link_field or "")
    if not ids:
        return []
    rows = client.batch_get_records(
        table.base_token, table.table_id, list(ids), field_names=list(table.safe_fields)
    )
    return [build_participation(table, row, CONFIDENCE_HIGH) for row in rows]


def _collect_from_scan(
    client: BitableClient, member: MergedMember, table: ActivityTable
) -> list[Participation]:
    """B 通道：孤儿活动表，按姓名关键词扫。"""
    if table.record_count_hint == 0:
        return []

    rows = client.search_records(
        table.base_token,
        table.table_id,
        field_names=list(table.safe_fields),
        keyword=member.name,
        search_fields=["姓名"],
    )
    out: list[Participation] = []
    for row in rows:
        confidence = judge_confidence(table, row, member)
        if confidence is not None:
            out.append(build_participation(table, row, confidence))
    return out


def collect_participations(
    client: BitableClient,
    registry: ActivityRegistry,
    member: MergedMember,
    *,
    max_workers: int = 8,
) -> tuple[list[Participation], list[str]]:
    """并发采集该人的全部活动参与记录。单表失败被隔离。"""
    tables = [t for key in member.base_keys for t in registry.tables_of(key)]
    if not tables:
        return [], []

    # 关联快通道的前提是这个人的主表关联列**确实有数据**。
    # 若一个关联都没有（早期数据没维护、或同步流程没跑），说明关联不可信，
    # 此时必须退化成「逐表扫描」，否则会把参与记录全部漏掉。
    linked_ok = any(
        member.link_targets_of(t.base_key, t.main_link_field or "")
        for t in tables
        if t.has_main_link
    )

    participations: list[Participation] = []
    errors: list[str] = []

    def run(table: ActivityTable) -> list[Participation]:
        if table.has_main_link and linked_ok:
            return _collect_from_linked(client, member, table)
        return _collect_from_scan(client, member, table)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(run, t): t for t in tables}
        for future in as_completed(futures):
            table = futures[future]
            try:
                participations.extend(future.result())
            except Exception as exc:  # noqa: BLE001 - 单表失败隔离
                errors.append(f"读取活动「{table.name}」失败：{exc}")

    participations.sort(
        key=lambda p: (p.event_time_ms or p.record_created_ms or 0, p.activity_name)
    )
    return participations, errors


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------


def query_member(
    client: BitableClient,
    registry: ActivityRegistry,
    specs: Sequence[Any],
    name: str,
    sid: str,
    *,
    max_workers: int = 8,
) -> MemberQueryResult:
    """按姓名 + 学号查询一个人的完整爱心足迹。"""
    records: list[MemberRecord] = []
    # 注册表构建期的失败（某库 403、某张表读不到）必须一并透出，
    # 否则用户会以为「没参加过」，而实际是「没查到」。
    errors: list[str] = list(registry.errors)
    searched: list[str] = []

    for spec in specs:
        searched.append(spec.key)
        try:
            records.extend(find_member_records(client, spec, registry, name, sid))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"查询 {spec.label} 主表失败：{exc}")

    if not records:
        return MemberQueryResult(
            found=False,
            member=None,
            participations=(),
            errors=tuple(errors),
            searched_bases=tuple(searched),
        )

    member = merge_members(records)
    assert member is not None  # records 非空

    participations, perr = collect_participations(
        client, registry, member, max_workers=max_workers
    )
    errors.extend(perr)

    return MemberQueryResult(
        found=True,
        member=member,
        participations=tuple(participations),
        errors=tuple(errors),
        searched_bases=tuple(searched),
    )
