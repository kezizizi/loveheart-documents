"""活动注册表：把飞书 Base 里「哪些表是活动问卷」自动探测出来。

核心洞察
--------
活动表 100% 可以通过「遍历 block 树 + 找有没有指向主表的 link 字段」识别；
「部组」不需要任何人工映射——它就藏在文件夹层级里。

因此本模块里**没有任何硬编码的 table_id 或活动名映射**。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Sequence

from config import (
    FIELD_TYPE_DATETIME,
    FIELD_TYPE_NUMBER,
    FORBIDDEN_TABLE_IDS,
    GROUP_COLORS,
    HOURS_RE,
    NON_ACTIVITY_LINK_FIELDS,
    NON_ACTIVITY_TABLE_NAME_PATTERNS,
    SENSITIVE_FIELDS,
    TIME_RE,
    TIME_STRONG_HINTS,
    BaseSpec,
    clean_activity_name,
    normalize_term,
)
from feishu import schema as S
from feishu.models import ActivityRegistry, ActivityTable
from feishu.protocol import BitableClient


# --------------------------------------------------------------------------
# 目录结构
# --------------------------------------------------------------------------


def folder_path_of(
    block_id: str | None, index: dict[str, dict[str, Any]]
) -> list[str]:
    """从最外层到最内层，返回该 block 的**祖先文件夹**名称列表。

    只统计 type == 'folder' 的祖先；返回顺序为 [顶层文件夹, ..., 直接父文件夹]。
    """
    names: list[str] = []
    seen: set[str] = set()
    cursor = block_id
    while cursor and cursor not in seen:
        seen.add(cursor)
        node = index.get(cursor)
        if node is None:
            break
        if node.get("type") == "folder":
            names.append(node.get("name") or "")
        cursor = node.get("parent_id")
    names.reverse()
    return [n for n in names if n]


def resolve_group(folder_path: Sequence[str]) -> str | None:
    """由文件夹层级推导部组。

    优先精确命中已知部组名；其次做子串匹配（如「组织部26春」）；
    都不命中时退回最外层文件夹名（如「1+n第二期」这类专项）。
    """
    for name in folder_path:
        if name in GROUP_COLORS:
            return name
    for name in folder_path:
        for group in GROUP_COLORS:
            if group in name:
                return group
    return folder_path[0] if folder_path else None


def resolve_term(folder_path: Sequence[str], table_name: str) -> str | None:
    """由文件夹（由深到浅）与表名推导学期标签。"""
    for name in reversed(list(folder_path)):
        term = normalize_term(name)
        if term:
            return term
    return normalize_term(table_name)


# --------------------------------------------------------------------------
# 字段探测
# --------------------------------------------------------------------------


def detect_hours_fields(fields: Sequence[dict[str, Any]]) -> tuple[str, ...]:
    """识别学时字段。字段名不统一（志愿时长 / 志愿学时 / 工时），靠正则兜。"""
    return tuple(
        f["name"]
        for f in fields
        if f.get("type") == FIELD_TYPE_NUMBER and HOURS_RE.search(f.get("name") or "")
    )


def detect_time_fields(fields: Sequence[dict[str, Any]]) -> tuple[str, ...]:
    """识别参与时间字段，强提示名（参与时间 / 活动日期）排前面。"""
    found = [
        f["name"]
        for f in fields
        if f.get("type") == FIELD_TYPE_DATETIME
        and TIME_RE.search(f.get("name") or "")
    ]

    def rank(name: str) -> tuple[int, int]:
        for i, hint in enumerate(TIME_STRONG_HINTS):
            if hint in name:
                return (i, len(name))
        return (len(TIME_STRONG_HINTS), len(name))

    return tuple(sorted(found, key=rank))


def detect_identity_fields(fields: Sequence[dict[str, Any]]) -> tuple[bool, bool]:
    has_sid = False
    has_name = False
    for f in fields:
        name = f.get("name") or ""
        if f.get("is_link"):
            continue
        if "学号" in name:
            has_sid = True
        if "姓名" in name:
            has_name = True
    return has_sid, has_name


def find_main_link_field(
    fields: Sequence[dict[str, Any]], main_table_id: str
) -> str | None:
    """找出活动表内指向主表的 link 字段名（通常叫「社员档案」）。"""
    for f in fields:
        if f.get("is_link") and f.get("link_table_id") == main_table_id:
            return f.get("name")
    return None


# --------------------------------------------------------------------------
# 主表 link 字段（活动清单的快速通道）
# --------------------------------------------------------------------------


def build_main_link_index(
    main_fields: Sequence[dict[str, Any]], main_table_id: str
) -> dict[str, str]:
    """主表 link 字段名 → 目标活动表 id。

    必须排除指向主表自身的自关联字段（26-27 库的「父记录」），否则会凭空
    多出一个「活动」。
    """
    index: dict[str, str] = {}
    for f in main_fields:
        if not f.get("is_link"):
            continue
        target = f.get("link_table_id")
        if not target or target == main_table_id:
            continue
        if (f.get("name") or "") in NON_ACTIVITY_LINK_FIELDS:
            continue
        index.setdefault(target, f.get("name") or "")
    return index


# --------------------------------------------------------------------------
# 注册表构建
# --------------------------------------------------------------------------


def _is_candidate_table(block: dict[str, Any], spec: BaseSpec) -> bool:
    if block.get("type") != "table":
        return False
    table_id = block.get("id")
    if not table_id or table_id == spec.main_table_id:
        return False
    if table_id in FORBIDDEN_TABLE_IDS:
        return False
    name = block.get("name") or ""
    return not any(p in name for p in NON_ACTIVITY_TABLE_NAME_PATTERNS)


def _probe_one(
    client: BitableClient,
    spec: BaseSpec,
    block: dict[str, Any],
    index: dict[str, dict[str, Any]],
    main_link_index: dict[str, str],
) -> ActivityTable | None:
    """探测单张候选表。不是活动表返回 None。"""
    table_id = block["id"]
    raw_name = block.get("name") or ""
    fields = client.list_fields(spec.base_token, table_id)

    link_field = find_main_link_field(fields, spec.main_table_id)
    if link_field is None:
        # 没有指回主表的 link 字段 → 不是活动问卷表
        return None

    main_link_field = main_link_index.get(table_id)
    display = clean_activity_name(raw_name) or clean_activity_name(
        main_link_field or ""
    )

    has_sid, has_name = detect_identity_fields(fields)
    folder_path = folder_path_of(block.get("parent_id"), index)
    safe_fields = tuple(
        f.get("name")
        for f in fields
        if f.get("name") and f.get("name") not in SENSITIVE_FIELDS
    )

    return ActivityTable(
        base_key=spec.key,
        base_token=spec.base_token,
        table_id=table_id,
        name=display or raw_name,
        group=resolve_group(folder_path),
        term=resolve_term(folder_path, raw_name),
        link_field=link_field,
        main_link_field=main_link_field,
        hours_fields=detect_hours_fields(fields),
        time_fields=detect_time_fields(fields),
        has_sid=has_sid,
        has_name=has_name,
        record_count_hint=S.safe_int(block.get("records_count")),
        safe_fields=safe_fields,
    )


def build_registry(
    client: BitableClient,
    specs: Sequence[BaseSpec],
    *,
    max_workers: int = 8,
    clock: Callable[[], float] = time.time,
) -> ActivityRegistry:
    """构建全量活动注册表。

    单张表失败不会中断整体：错误进入 ``errors``，该表被跳过。
    """
    tables: list[ActivityTable] = []
    errors: list[str] = []
    main_fields_by_base: list[tuple[str, tuple[str, ...]]] = []

    for spec in specs:
        try:
            blocks = client.list_blocks(spec.base_token)
        except Exception as exc:  # noqa: BLE001 - 单库失败不影响另一库
            errors.append(f"[{spec.label}] 读取 block 树失败：{exc}")
            continue

        index = {b["id"]: b for b in blocks if b.get("id")}

        try:
            main_fields = client.list_fields(spec.base_token, spec.main_table_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"[{spec.label}] 读取主表字段失败：{exc}")
            main_fields = []

        main_fields_by_base.append(
            (spec.key, tuple(f.get("name") or "" for f in main_fields if f.get("name")))
        )
        main_link_index = build_main_link_index(main_fields, spec.main_table_id)

        candidates = [b for b in blocks if _is_candidate_table(b, spec)]
        if not candidates:
            continue

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _probe_one, client, spec, block, index, main_link_index
                ): block
                for block in candidates
            }
            for future in as_completed(futures):
                block = futures[future]
                try:
                    table = future.result()
                except Exception as exc:  # noqa: BLE001 - 单表失败隔离
                    errors.append(
                        f"[{spec.label}] 探测表「{block.get('name')}」"
                        f"（{block.get('id')}）失败：{exc}"
                    )
                    continue
                if table is not None:
                    tables.append(table)

    tables.sort(key=lambda t: (t.term or "", t.group or "", t.name))
    return ActivityRegistry(
        tables=tuple(tables),
        built_at=clock(),
        errors=tuple(errors),
        main_fields=tuple(main_fields_by_base),
    )
