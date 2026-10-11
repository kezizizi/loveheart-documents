"""内存版 BitableClient：本地演示与测试共用。

放在包内（而不是 tests 里）有两个原因：
1. 演示模式 ``AX_DEMO=1`` 可以让 UI 在没有真实密钥时也能跑起来；
2. 测试与演示使用**同一份**内存实现，避免两套假实现互相漂移。

它从「录制的原始 JSON fixture」读取数据，并在出口处过一遍
:mod:`feishu.schema` 归一化，保证与真实客户端返回同一种形状。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from feishu import schema as S

DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def load_fixture(name: str, fixture_dir: Path | None = None) -> Any:
    path = (fixture_dir or DEFAULT_FIXTURE_DIR) / name
    return json.loads(path.read_text(encoding="utf-8"))


def _flatten(raw: dict[str, dict[str, list]]) -> dict[tuple[str, str], list]:
    out: dict[tuple[str, str], list] = {}
    for base_token, tables in raw.items():
        for table_id, items in tables.items():
            out[(base_token, table_id)] = items
    return out


class FakeBitableClient:
    """内存版多维表格客户端。"""

    def __init__(
        self,
        *,
        blocks: dict[str, list[dict]] | None = None,
        fields: dict[tuple[str, str], list[dict]] | None = None,
        records: dict[tuple[str, str], list[dict]] | None = None,
        batch_records: dict[tuple[str, str], list[dict]] | None = None,
        fail_tables: Sequence[str] = (),
        fail_records: Sequence[str] = (),
    ) -> None:
        self.blocks = blocks or {}
        self.fields = fields or {}
        self.records = records or {}
        self.batch_records = batch_records or {}
        self.fail_tables = set(fail_tables)
        self.fail_records = set(fail_records)
        self.calls: list[tuple[str, ...]] = []
        # 记录每次请求实际投影的字段名，供隐私回归测试断言
        self.projections: list[tuple[str, str, tuple[str, ...]]] = []

    # ---------------------------------------------------------- 协议实现

    def list_blocks(self, base_token: str, *, parent_id=None, block_type=None):
        self.calls.append(("list_blocks", base_token))
        items = self.blocks.get(base_token, [])
        if parent_id is not None:
            items = [b for b in items if b.get("parent_id") == parent_id]
        if block_type is not None:
            items = [b for b in items if b.get("type") == block_type]
        return S.normalize_blocks(items)

    def list_fields(self, base_token: str, table_id: str):
        self.calls.append(("list_fields", base_token, table_id))
        if table_id in self.fail_tables:
            raise RuntimeError(f"模拟失败：{table_id}")
        return S.normalize_fields(self.fields.get((base_token, table_id), []))

    def search_records(
        self,
        base_token: str,
        table_id: str,
        *,
        field_names=None,
        keyword=None,
        search_fields=None,
        automatic_fields=False,
        limit=100,
    ):
        self.calls.append(("search_records", base_token, table_id))
        self.projections.append(("search_records", table_id, tuple(field_names or ())))
        if table_id in self.fail_tables or table_id in self.fail_records:
            raise RuntimeError(f"模拟失败：{table_id}")

        rows = list(self.records.get((base_token, table_id), []))
        if keyword and search_fields:
            rows = [
                r
                for r in rows
                if any(
                    str(keyword) in S.cell_text((r.get("fields") or {}).get(f, ""))
                    for f in search_fields
                )
            ]
        return S.normalize_records(rows)

    def batch_get_records(
        self, base_token: str, table_id: str, record_ids, *, field_names=None
    ):
        self.calls.append(("batch_get_records", base_token, table_id))
        self.projections.append(("batch_get_records", table_id, tuple(field_names or ())))
        if table_id in self.fail_records:
            raise RuntimeError(f"模拟失败：{table_id}")

        rows = self.batch_records.get((base_token, table_id))
        if rows is None:
            rows = self.records.get((base_token, table_id), [])
        wanted = set(record_ids)
        return S.normalize_records([r for r in rows if r.get("record_id") in wanted])


def build_fixture_client(
    *,
    blocks_file: str = "blocks.json",
    fields_file: str = "fields.json",
    records_file: str | None = None,
    fail_tables: Sequence[str] = (),
    fail_records: Sequence[str] = (),
    fixture_dir: Path | None = None,
) -> FakeBitableClient:
    """用 JSON fixture 装出一个可用的客户端。"""
    base = fixture_dir or DEFAULT_FIXTURE_DIR
    return FakeBitableClient(
        blocks=load_fixture(blocks_file, base),
        fields=_flatten(load_fixture(fields_file, base)),
        records=_flatten(load_fixture(records_file, base)) if records_file else {},
        fail_tables=fail_tables,
        fail_records=fail_records,
    )
