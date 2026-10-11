"""把飞书返回的原始 JSON 归一化成统一形状。

飞书存在两套接口形态（旧 bitable v1 / 新 base v3），键名不一致；
link 字段的属性键名也有 ``table_id`` 与 ``link_table`` 两种。
本模块把这些差异全部吸收掉，上层只面对一种形状。
"""

from __future__ import annotations

from typing import Any, Iterable


def safe_int(value: Any) -> int | None:
    """宽松转 int，失败返回 None。"""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _first(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


# --------------------------------------------------------------------------
# Block
# --------------------------------------------------------------------------


def normalize_block(raw: dict[str, Any]) -> dict[str, Any]:
    """Block → {id, type, name, parent_id, records_count}。"""
    return {
        "id": _first(raw, "id", "block_id", "block_token"),
        "type": _first(raw, "type", "block_type"),
        "name": _first(raw, "name", "block_name", "table_name") or "",
        "parent_id": _first(raw, "parent_id", "parent_block_id"),
        "records_count": _first(raw, "records_count", "record_count"),
    }


def normalize_blocks(raw: Any) -> list[dict[str, Any]]:
    """兼容 ``data.blocks`` / ``data.items`` / 裸列表。"""
    if isinstance(raw, dict):
        raw = raw.get("blocks") or raw.get("items") or []
    if not isinstance(raw, list):
        return []
    return [normalize_block(b) for b in raw if isinstance(b, dict)]


# --------------------------------------------------------------------------
# Field
# --------------------------------------------------------------------------


def field_id(raw: dict[str, Any]) -> str | None:
    return _first(raw, "field_id", "id")


def field_name(raw: dict[str, Any]) -> str:
    return _first(raw, "field_name", "name") or ""


def field_type(raw: dict[str, Any]) -> int | None:
    t = _first(raw, "type", "field_type")
    try:
        return int(t)
    except (TypeError, ValueError):
        return None


def field_property(raw: dict[str, Any]) -> dict[str, Any]:
    prop = raw.get("property")
    return prop if isinstance(prop, dict) else {}


def is_link_field(raw: dict[str, Any]) -> bool:
    """link 字段类型编号：18（单向）/ 21（双向）。"""
    return field_type(raw) in (18, 21)


def field_link_table_id(raw: dict[str, Any]) -> str | None:
    """link 字段指向的目标表 id（兼容两种键名）。"""
    prop = field_property(raw)
    return _first(prop, "table_id", "link_table", "linked_table_id") or raw.get(
        "link_table"
    )


def normalize_field(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": field_id(raw),
        "name": field_name(raw),
        "type": field_type(raw),
        "property": field_property(raw),
        "link_table_id": field_link_table_id(raw),
        "is_link": is_link_field(raw),
        "is_primary": bool(raw.get("is_primary", False)),
    }


def normalize_fields(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("fields") or raw.get("items") or []
    if not isinstance(raw, list):
        return []
    return [normalize_field(f) for f in raw if isinstance(f, dict)]


# --------------------------------------------------------------------------
# Record
# --------------------------------------------------------------------------


def record_id(raw: dict[str, Any]) -> str | None:
    return _first(raw, "record_id", "id")


def record_fields(raw: dict[str, Any]) -> dict[str, Any]:
    fields = raw.get("fields")
    return fields if isinstance(fields, dict) else {}


def record_created_ms(raw: dict[str, Any]) -> int | None:
    v = _first(raw, "created_time", "created_at", "record_created_time")
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def normalize_record(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_id": record_id(raw),
        "fields": record_fields(raw),
        "created_time_ms": record_created_ms(raw),
    }


def normalize_records(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("records") or raw.get("items") or []
    if not isinstance(raw, list):
        return []
    return [normalize_record(r) for r in raw if isinstance(r, dict)]


# --------------------------------------------------------------------------
# 单元格取值
# --------------------------------------------------------------------------


def cell_text(value: Any) -> str:
    """把任意单元格值转成可读文本。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    # bool 是 int 的子类，必须先判，否则复选框会显示成 "True" / "False"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return _number_text(value)
    if isinstance(value, dict):
        for key in ("text", "name", "value", "en_name"):
            if key in value and isinstance(value[key], (str, int, float)):
                return str(value[key]).strip()
        arr = value.get("text_arr")
        if isinstance(arr, list):
            return "".join(str(x) for x in arr).strip()
        return ""
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            t = cell_text(item)
            if t:
                parts.append(t)
        return ", ".join(parts)
    return str(value).strip()


def _number_text(v: int | float) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def cell_number(value: Any) -> float | None:
    """把单元格值转成数字，失败返回 None。"""
    import re

    if value is None or value == "" or value == []:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("value", "text", "number"):
            if key in value:
                return cell_number(value[key])
        return None
    if isinstance(value, list):
        for item in value:
            n = cell_number(item)
            if n is not None:
                return n
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(m.group()) if m else None


def cell_datetime_ms(value: Any) -> int | None:
    """日期单元格 → 毫秒时间戳。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        # 秒级时间戳（10 位）自动升到毫秒
        if 0 < v < 1e11:
            return int(v * 1000)
        return int(v)
    if isinstance(value, dict):
        for key in ("value", "timestamp", "time"):
            if key in value:
                return cell_datetime_ms(value[key])
        return None
    if isinstance(value, list):
        for item in value:
            ts = cell_datetime_ms(item)
            if ts is not None:
                return ts
        return None
    return None


def cell_link_record_ids(value: Any) -> list[str]:
    """link 单元格 → 目标 record_id 列表。

    ⚠️ 真实飞书返回的键名是 ``link_record_ids``（实测），旧文档/旧样例里
    常见 ``record_ids``。两种都要认，否则关联匹配会静默失效。
    """
    ids: list[str] = []
    id_keys = ("link_record_ids", "record_ids", "record_id", "ids")

    def _walk(v: Any) -> None:
        if v is None:
            return
        if isinstance(v, str):
            if v.startswith("rec"):
                ids.append(v)
            return
        if isinstance(v, dict):
            matched = False
            for key in id_keys:
                if key in v:
                    _walk(v[key])
                    matched = True
            # 只有在没有 id 类键时才下探展示用的字段（text 里不会出现 rec 开头串）
            if not matched:
                for key in ("records", "value"):
                    if key in v:
                        _walk(v[key])
            return
        if isinstance(v, list):
            for item in v:
                _walk(item)

    _walk(value)

    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def iter_cell_values(fields: dict[str, Any]) -> Iterable[tuple[str, Any]]:
    return ((k, v) for k, v in fields.items())
