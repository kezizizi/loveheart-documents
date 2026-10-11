"""UI 层：会话状态与缓存装配。

缓存策略
--------
* 活动注册表：``st.cache_data``，TTL 24 小时，**全局共用**（不带会话级 key）。
* 成员查询结果：``st.cache_data``，TTL 10 分钟。
* 手动「刷新数据」= 清掉上面两个缓存 + 丢弃当前结果。

为什么注册表缓存必须是全局的
----------------------------
一次注册表构建要遍历两个库、逐张活动表读字段，**约 175 次请求**；
而飞书 API 调用量有月度上限。若缓存键里掺入会话级 nonce，每个新访客都会
付一次这个代价。所以 ``load_registry`` 刻意**不收任何参数** ——
缓存键里没有能区分会话的东西，所有会话共用同一份。
代价是「刷新数据」对所有人都生效，这是有意接受的。

关键约束：**致命错误直接抛**（``st.cache_data`` 不会缓存抛异常的结果），
因此不会出现「错误结果被缓存住」的经典事故。
"""

from __future__ import annotations

import os
from datetime import date, datetime

import streamlit as st

from config import (
    DEFAULT_MEMBER_TTL,
    DEFAULT_REGISTRY_TTL,
    Settings,
    load_settings,
)
from feishu import BitableClient, FeishuHttpClient
from feishu.member_query import MemberQueryResult, query_member
from feishu.models import ActivityRegistry
from feishu.registry import build_registry
from services.schema_refresh import with_schema_refresh

DEMO_ENV = "AX_DEMO"
_RESULT_KEY = "ax_result"
_QUERY_KEY = "ax_query"


def demo_mode() -> bool:
    """``AX_DEMO=1`` 时使用内存假数据，便于没有密钥也能预览界面。"""
    return os.environ.get(DEMO_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------
# 资源
# --------------------------------------------------------------------------


@st.cache_resource(show_spinner=False)
def get_settings() -> Settings:
    return load_settings(require_credentials=not demo_mode())


@st.cache_resource(show_spinner=False)
def get_client() -> BitableClient:
    if demo_mode():
        from feishu.fake import build_fixture_client

        return build_fixture_client(records_file="records.json")
    settings = get_settings()
    return FeishuHttpClient(
        settings.app_id, settings.app_secret, max_workers=settings.max_workers
    )


@st.cache_data(ttl=DEFAULT_REGISTRY_TTL, show_spinner=False)
def load_registry() -> ActivityRegistry:
    """全量活动注册表。

    ⚠️ **刻意不收任何参数**：缓存键里一旦掺进会话级的东西（例如 nonce），
    每个新访客都会重建一次（约 175 次请求），而飞书 API 有月度配额。
    `tests/test_state_cache.py` 守着这条。
    """
    settings = get_settings()
    return build_registry(
        get_client(),
        settings.bases,
        max_workers=settings.max_workers,
    )


@st.cache_data(ttl=DEFAULT_MEMBER_TTL, show_spinner=False)
def run_query(name: str, sid: str) -> MemberQueryResult:
    settings = get_settings()
    client = get_client()

    def run() -> MemberQueryResult:
        # 每次都在 run 内部取注册表：重建之后这里才能拿到新的那份
        return query_member(
            client,
            load_registry(),
            settings.bases,
            name,
            sid,
            max_workers=settings.max_workers,
        )

    def rebuild() -> None:
        load_registry.clear()

    # 骨干改名或删列之后，缓存了 24 小时的旧字段名会让查询整体失败。
    # 这里丢掉过时的注册表重试一次，用户端是无感的。
    return with_schema_refresh(run, rebuild)


# --------------------------------------------------------------------------
# 会话状态
# --------------------------------------------------------------------------


def refresh() -> None:
    """重建缓存：丢掉注册表与查询结果，下一次访问重新探测。

    注册表缓存是**全局**的，所以这个动作对所有人都生效 —— 这是刻意的，
    见模块开头的说明。只清我们自己的两个缓存，不去动 ``st.cache_data`` 里
    别人的东西。
    """
    st.session_state.pop(_RESULT_KEY, None)
    st.session_state.pop(_QUERY_KEY, None)
    load_registry.clear()
    run_query.clear()


def stored_result() -> MemberQueryResult | None:
    return st.session_state.get(_RESULT_KEY)


def store_result(result: MemberQueryResult | None, query: tuple[str, str] | None) -> None:
    st.session_state[_RESULT_KEY] = result
    st.session_state[_QUERY_KEY] = query


def stored_query() -> tuple[str, str] | None:
    value = st.session_state.get(_QUERY_KEY)
    if isinstance(value, tuple) and len(value) == 2:
        return value
    return None


def data_as_of() -> date:
    try:
        built = load_registry().built_at
    except Exception:  # noqa: BLE001 - 拿不到就用今天
        return date.today()
    return datetime.fromtimestamp(built).date()
