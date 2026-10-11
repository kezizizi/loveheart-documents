"""结构变更自愈：命中「表结构已变」就重建依赖，再重试一次。

为什么单独成模块
----------------
重试语义值得被测试，而它原本嵌在 Streamlit 的缓存函数里 —— 那里只能靠
手工点界面来验证。抽成纯函数后，`tests/test_schema_refresh.py` 可以直接
覆盖「只重试一次」「其余错误不重建」这些规则。

为什么需要它
------------
活动注册表缓存 24 小时。骨干把某一列改名或删掉之后，缓存里的旧字段名会让
那张表的查询整体报错。此时唯一正确的动作是**丢掉注册表缓存、重新探测一次**，
而不是把未知字段悄悄丢掉继续查 —— 后者会让学时静默变成 0。
"""

from __future__ import annotations

from typing import Callable, TypeVar

from feishu.client import SchemaChangedError

T = TypeVar("T")


def with_schema_refresh(run: Callable[[], T], rebuild: Callable[[], None]) -> T:
    """跑 ``run()``；若命中 :class:`SchemaChangedError`，``rebuild()`` 后**只**重试一次。

    ``rebuild`` 负责让缓存失效并重建依赖（注册表）。第二次仍失败就让它抛出去 ——
    无限重试只会把飞书 API 配额烧光，还掩盖真正的问题。
    """
    try:
        return run()
    except SchemaChangedError:
        rebuild()
        return run()
