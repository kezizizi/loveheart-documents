"""数据层的可注入接口。

这是整个系统的**唯一缝（seam）**：上层只依赖这个 Protocol，
真实实现走飞书 HTTP，测试实现读本地 fixture。
"""

from __future__ import annotations

from typing import Any, Protocol, Sequence, runtime_checkable


@runtime_checkable
class BitableClient(Protocol):
    """多维表格客户端的最小能力集。"""

    def list_blocks(
        self,
        base_token: str,
        *,
        parent_id: str | None = None,
        block_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """列出 Base 的 Block（folder / table / docx / dashboard / workflow）。"""
        ...

    def list_fields(self, base_token: str, table_id: str) -> list[dict[str, Any]]:
        """列出某张表的全部字段定义。"""
        ...

    def search_records(
        self,
        base_token: str,
        table_id: str,
        *,
        field_names: Sequence[str] | None = None,
        keyword: str | None = None,
        search_fields: Sequence[str] | None = None,
        automatic_fields: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """按关键词搜索记录，返回全部命中页（内部自动翻页）。"""
        ...

    def batch_get_records(
        self,
        base_token: str,
        table_id: str,
        record_ids: Sequence[str],
        *,
        field_names: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """按 record_id 批量取记录（自动分批）。"""
        ...
