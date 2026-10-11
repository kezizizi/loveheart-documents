"""pytest 公用装置：假会话、假客户端、fixture 装载器。

测试**绝不触网**。所有网络行为都由 :class:`RecordingSession` 打桩，
所有数据层行为都由 :class:`FakeBitableClient` 打桩。

内存版客户端实现放在 :mod:`feishu.fake`，演示模式与测试共用同一份，
避免两套假实现互相漂移。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

import pytest

from feishu.fake import (  # noqa: F401 - 供测试直接 import
    FakeBitableClient,
    build_fixture_client,
    load_fixture,
)

# --------------------------------------------------------------------------
# 常量：测试里用到的两库坐标
# --------------------------------------------------------------------------

BASE_26_27 = "P0lpbqm44a21zKswi6tcbEj2nHc"
MAIN_26_27 = "tbliPIl5lYefS4Nr"
BASE_25_26 = "NPcMbmMI6a06jmsaXoscwLcqnBf"
MAIN_25_26 = "tblE5QYLVyf7YBmE"


# --------------------------------------------------------------------------
# 假 HTTP 会话
# --------------------------------------------------------------------------


class FakeResponse:
    def __init__(
        self, status_code: int, body: dict[str, Any], headers: dict | None = None
    ):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return self._body


class RecordingSession:
    """记录每次请求，并按顺序或按处理器返回预设响应。"""

    def __init__(
        self,
        handler: Callable[[str, str, dict[str, Any]], FakeResponse] | None = None,
        responses: Iterable[FakeResponse] | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._handler = handler
        self._queue = list(responses or [])

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if self._handler is not None:
            return self._handler(method, url, kwargs)
        if self._queue:
            return self._queue.pop(0)
        raise AssertionError(f"没有预设响应可返回：{method} {url}")

    # -- 便捷断言 -------------------------------------------------------

    def count(self, *, contains: str = "") -> int:
        return sum(1 for c in self.calls if contains in c["url"])

    def token_calls(self) -> int:
        return self.count(contains="tenant_access_token")


def ok(
    data: dict[str, Any], status: int = 200, headers: dict | None = None
) -> FakeResponse:
    return FakeResponse(status, {"code": 0, "msg": "success", "data": data}, headers)


def err(
    code: int,
    msg: str = "error",
    status: int = 200,
    headers: dict | None = None,
) -> FakeResponse:
    return FakeResponse(status, {"code": code, "msg": msg}, headers)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def make_session() -> Callable[..., RecordingSession]:
    return RecordingSession


@pytest.fixture
def fixture_loader() -> Callable[[str], Any]:
    return load_fixture


@pytest.fixture
def fixture_client() -> FakeBitableClient:
    return build_fixture_client()


@pytest.fixture
def fixture_client_with_records() -> FakeBitableClient:
    return build_fixture_client(records_file="records.json")


@pytest.fixture
def specs() -> tuple:
    from config import BaseSpec

    return (
        BaseSpec(
            key="base_25_26",
            label="25-26 学年",
            base_token=BASE_25_26,
            main_table_id=MAIN_25_26,
        ),
        BaseSpec(
            key="base_26_27",
            label="26-27 学年",
            base_token=BASE_26_27,
            main_table_id=MAIN_26_27,
        ),
    )


__all__ = [
    "BASE_25_26",
    "BASE_26_27",
    "FakeBitableClient",
    "FakeResponse",
    "MAIN_25_26",
    "MAIN_26_27",
    "RecordingSession",
    "build_fixture_client",
    "err",
    "load_fixture",
    "ok",
]
