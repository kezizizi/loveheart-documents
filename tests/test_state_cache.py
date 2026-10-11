"""缓存键守卫：注册表缓存必须是**全局**的。

一次注册表构建要遍历两个库、逐张活动表读字段，**约 175 次请求**；
而飞书 API 调用量有月度上限。缓存键里一旦掺进会话级的东西（曾经是 nonce），
每个新访客都要付一次这个代价 —— 从「每天一次」变成「每个访客一次」。

这里不测 Streamlit 的缓存行为本身（那要在运行时才成立），只盯住
**签名里不许出现能区分会话的参数**这一条结构性约束。
"""

from __future__ import annotations

import inspect

from ui import state


def _required_params(fn) -> list[str]:
    return [
        p.name
        for p in inspect.signature(fn).parameters.values()
        if p.default is inspect.Parameter.empty
        and p.kind
        in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
    ]


def test_registry_cache_key_cannot_distinguish_sessions():
    """🚩 红线：``load_registry`` 必须不收参数。

    收了参数（哪怕叫 nonce）缓存键就会按会话分裂，每个新访客重建一次注册表
    ≈ 175 次请求，直接吃掉飞书月度配额。
    """
    assert _required_params(state.load_registry) == [], (
        "load_registry 不能接收参数，否则注册表缓存会按会话分裂、把 API 配额烧光"
    )


def test_query_cache_is_keyed_only_by_identity():
    """查询缓存只按「姓名 + 学号」区分，不含任何会话标识。"""
    assert _required_params(state.run_query) == ["name", "sid"]


def test_refresh_clears_only_our_own_caches():
    """「刷新数据」只清我们自己的两个缓存，不去动 cache_data 里别人的东西。"""
    assert hasattr(state.load_registry, "clear")
    assert hasattr(state.run_query, "clear")

    source = inspect.getsource(state.refresh)
    assert "cache_data.clear" not in source, (
        "刷新应只清 load_registry / run_query，不要顺手清空整个 cache_data"
    )
