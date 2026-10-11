"""UI 层：会话状态与缓存装配。

缓存策略
--------
* 活动注册表：``st.cache_data``，TTL 24 小时，key 里带 ``nonce``。
* 成员查询结果：``st.cache_data``，TTL 10 分钟，同上。
* 手动「刷新数据」= 换一个 ``nonce`` + 清空 cache_data。

关键约束：**致命错误直接抛**（``st.cache_data`` 不会缓存抛异常的结果），
因此不会出现「错误结果被缓存住」的经典事故。
"""

from __future__ import annotations

import os
import uuid
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
_NONCE_KEY = "ax_nonce"
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
def load_registry(nonce: str) -> ActivityRegistry:
    settings = get_settings()
    return build_registry(
        get_client(),
        settings.bases,
        max_workers=settings.max_workers,
    )


@st.cache_data(ttl=DEFAULT_MEMBER_TTL, show_spinner=False)
def run_query(nonce: str, name: str, sid: str) -> MemberQueryResult:
    settings = get_settings()
    client = get_client()

    def run() -> MemberQueryResult:
        # 每次都在 run 内部取注册表：重建之后这里才能拿到新的那份
        return query_member(
            client,
            load_registry(nonce),
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


def get_nonce() -> str:
    if _NONCE_KEY not in st.session_state:
        st.session_state[_NONCE_KEY] = uuid.uuid4().hex
    return str(st.session_state[_NONCE_KEY])


def refresh() -> None:
    """重建缓存：换 nonce + 清空 cache_data + 丢弃当前结果。"""
    st.session_state[_NONCE_KEY] = uuid.uuid4().hex
    st.session_state.pop(_RESULT_KEY, None)
    st.session_state.pop(_QUERY_KEY, None)
    st.cache_data.clear()


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


def data_as_of(nonce: str) -> date:
    try:
        built = load_registry(nonce).built_at
    except Exception:  # noqa: BLE001 - 拿不到就用今天
        return date.today()
    return datetime.fromtimestamp(built).date()
