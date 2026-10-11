"""结构变更自愈：命中「表结构已变」时重建依赖并**只重试一次**。

测试缝：``services.schema_refresh.with_schema_refresh`` 这个纯函数。
把它独立出来，是为了不依赖 Streamlit 就能验证重试语义 ——
放在 ``ui/state.py`` 里就只能靠手工点界面来验了。
"""

from __future__ import annotations

import pytest

from feishu.client import FeishuError, SchemaChangedError
from services.schema_refresh import with_schema_refresh


def test_passes_through_without_rebuilding_on_success():
    events: list[str] = []

    def run() -> int:
        events.append("run")
        return 42

    assert with_schema_refresh(run, lambda: events.append("rebuild")) == 42
    assert events == ["run"], "正常路径不应有任何额外开销"


def test_rebuilds_then_retries_once_on_schema_change():
    events: list[str] = []

    def run() -> str:
        events.append("run")
        if events.count("run") == 1:
            raise SchemaChangedError("字段被改名了")
        return "ok"

    assert with_schema_refresh(run, lambda: events.append("rebuild")) == "ok"
    assert events == ["run", "rebuild", "run"]


def test_retries_at_most_once():
    runs = {"n": 0}

    def run() -> str:
        runs["n"] += 1
        raise SchemaChangedError("字段被改名了")

    with pytest.raises(SchemaChangedError):
        with_schema_refresh(run, lambda: None)

    assert runs["n"] == 2, "初次 + 一次重试，不允许无限重试"


def test_other_errors_propagate_without_rebuilding():
    rebuilt: list[str] = []

    def run() -> str:
        raise FeishuError("RolePermNotAllow", code=1254302)

    with pytest.raises(FeishuError):
        with_schema_refresh(run, lambda: rebuilt.append("rebuild"))

    assert rebuilt == [], "只有结构变更才值得重建，其余错误重建也是白搭"
