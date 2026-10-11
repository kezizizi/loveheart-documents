"""M1 红-绿切片：认证、重试、隐私守卫、归一化。

测试缝：``FeishuHttpClient`` 注入的假会话（session / clock / sleeper）。
"""

from __future__ import annotations

import pytest

from feishu.client import (
    FeishuError,
    FeishuHttpClient,
    SensitiveFieldError,
    assert_names_allowed,
    project,
)
from tests.conftest import FakeResponse, RecordingSession, err, ok

BASE = "basetoken"
TABLE = "tblMain"


def make_client(session: RecordingSession, slept: list[float] | None = None) -> FeishuHttpClient:
    slept = slept if slept is not None else []
    return FeishuHttpClient(
        "cli_test",
        "secret_test",
        session=session,
        sleeper=slept.append,
        clock=lambda: 1_000_000.0,
    )


def token_ok(expire: int = 7200) -> FakeResponse:
    """token 接口的响应是**扁平**的，不嵌在 data 里。"""
    return FakeResponse(
        200,
        {"code": 0, "msg": "ok", "tenant_access_token": "t-abc", "expire": expire},
    )


# --------------------------------------------------------------------------
# 切片 1：token 缓存复用
# --------------------------------------------------------------------------


def test_token_is_fetched_once_and_reused():
    def handler(method: str, url: str, kwargs: dict):
        if "tenant_access_token" in url:
            return token_ok()
        if "blocks/list" in url:
            return ok({"blocks": []})
        raise AssertionError(url)

    session = RecordingSession(handler=handler)
    client = make_client(session)

    client.list_blocks(BASE)
    client.list_blocks(BASE)
    client.list_blocks(BASE)

    assert session.token_calls() == 1, "token 应当只取一次"
    assert session.count(contains="blocks/list") == 3


def test_token_is_renewed_when_close_to_expiry():
    """临近过期（不足 5 分钟）应重新获取。"""
    now = [1_000_000.0]

    def handler(method: str, url: str, kwargs: dict):
        if "tenant_access_token" in url:
            return token_ok(expire=100)
        return ok({"blocks": []})

    session = RecordingSession(handler=handler)
    client = FeishuHttpClient(
        "cli_test",
        "secret_test",
        session=session,
        sleeper=lambda _s: None,
        clock=lambda: now[0],
    )

    client.list_blocks(BASE)
    now[0] += 60  # 100 - 60 = 40 < 300，已进入续期窗口
    client.list_blocks(BASE)

    assert session.token_calls() == 2, "临近过期必须续期"


# --------------------------------------------------------------------------
# 切片 2：429 退避重试
# --------------------------------------------------------------------------


def test_retry_on_429_then_success():
    slept: list[float] = []
    state = {"n": 0}

    def handler(method: str, url: str, kwargs: dict):
        if "tenant_access_token" in url:
            return token_ok()
        state["n"] += 1
        if state["n"] == 1:
            return FakeResponse(429, {"code": 99991400, "msg": "too many requests"})
        return ok({"blocks": [{"id": "tbl1", "type": "table", "name": "A"}]})

    session = RecordingSession(handler=handler)
    client = make_client(session, slept)

    blocks = client.list_blocks(BASE)

    assert len(blocks) == 1
    assert blocks[0]["id"] == "tbl1"
    assert len(slept) == 1, "应当恰好退避一次"
    assert 0 < slept[0] <= 8.0


def test_retry_respects_retry_after_header():
    slept: list[float] = []
    state = {"n": 0}

    def handler(method: str, url: str, kwargs: dict):
        if "tenant_access_token" in url:
            return token_ok()
        state["n"] += 1
        if state["n"] == 1:
            return FakeResponse(
                429, {"code": 99991400, "msg": "slow down"}, {"Retry-After": "2"}
            )
        return ok({"blocks": []})

    client = make_client(RecordingSession(handler=handler), slept)
    client.list_blocks(BASE)

    assert slept == [2.0], "应优先采用 Retry-After"


def test_403_is_not_retried():
    """权限错误不重试。会换接口降级，但对同一个端点只打一次。"""
    state = {"n": 0}

    def handler(method: str, url: str, kwargs: dict):
        if "tenant_access_token" in url:
            return token_ok()
        state["n"] += 1
        return FakeResponse(403, {"code": 99991663, "msg": "forbidden"})

    session = RecordingSession(handler=handler)
    client = make_client(session)

    with pytest.raises(FeishuError):
        client.list_blocks(BASE)

    assert session.count(contains="blocks/list") == 1, "权限错误不应重试同一条请求"


def test_blocks_fall_back_to_flat_table_list_when_scope_missing():
    """缺少 base:block:read 时降级为「旧版列出全部表」，并标记降级。"""

    def handler(method: str, url: str, kwargs: dict):
        if "tenant_access_token" in url:
            return token_ok()
        if "/base/v3/" in url:
            return FakeResponse(403, {"code": 99991672, "msg": "scope base:block:read required"})
        if "/bitable/v1/apps/" in url and url.endswith("/tables"):
            return ok(
                {
                    "items": [
                        {"table_id": "tblA", "name": "社员档案"},
                        {"table_id": "tblB", "name": "2025秋金盲杖反馈、学时问卷"},
                    ],
                    "has_more": False,
                }
            )
        raise AssertionError(url)

    session = RecordingSession(handler=handler)
    client = make_client(session)

    blocks = client.list_blocks(BASE, block_type="table")

    assert [b["id"] for b in blocks] == ["tblA", "tblB"]
    assert all(b["type"] == "table" for b in blocks)
    assert all(b["parent_id"] is None for b in blocks)
    assert client.blocks_degraded is True


# --------------------------------------------------------------------------
# 切片 3：新旧接口回退
# --------------------------------------------------------------------------


def test_batch_get_falls_back_to_v1_when_scope_missing():
    """⚠️ 真实事故：batch_get 若不回退，「关联快通道」会整体失效、漏掉全部活动。"""

    def handler(method: str, url: str, kwargs: dict):
        if "tenant_access_token" in url:
            return token_ok()
        if "/base/v3/" in url:
            return FakeResponse(
                403, {"code": 99991672, "msg": "scope base:record:read required"}
            )
        if "/bitable/v1/" in url:
            return ok(
                {
                    "items": [
                        {
                            "record_id": "recA1",
                            "fields": {"姓名": [{"text": "张三"}], "志愿学时": 3},
                        }
                    ]
                }
            )
        raise AssertionError(url)

    session = RecordingSession(handler=handler)
    client = make_client(session)

    rows = client.batch_get_records(BASE, TABLE, ["recA1"], field_names=["姓名", "志愿学时"])

    assert [r["record_id"] for r in rows] == ["recA1"]
    assert rows[0]["fields"]["志愿学时"] == 3
    assert session.count(contains="/bitable/v1/") == 1


def test_fields_falls_back_to_v1_when_v3_forbidden():
    def handler(method: str, url: str, kwargs: dict):
        if "tenant_access_token" in url:
            return token_ok()
        if "/base/v3/" in url:
            return FakeResponse(403, {"code": 99991672, "msg": "scope not opened"})
        if "/bitable/v1/" in url:
            return ok(
                {
                    "items": [
                        {"field_id": "fld1", "field_name": "姓名", "type": 1},
                    ],
                    "has_more": False,
                }
            )
        raise AssertionError(url)

    session = RecordingSession(handler=handler)
    client = make_client(session)

    fields = client.list_fields(BASE, TABLE)

    assert [f["name"] for f in fields] == ["姓名"]
    assert session.count(contains="/bitable/v1/") == 1, "应当回退到旧版接口"

    # 第二次调用应当直接走旧版，不再试探新版
    client.list_fields(BASE, TABLE)
    assert session.count(contains="/base/v3/") == 1


# --------------------------------------------------------------------------
# 切片 4：隐私守卫
# --------------------------------------------------------------------------


def test_outbound_sensitive_field_name_raises():
    with pytest.raises(SensitiveFieldError):
        assert_names_allowed(["姓名", "您的身份证"])


def test_inbound_sensitive_field_raises():
    with pytest.raises(SensitiveFieldError):
        project({"姓名": "张三", "您的微信号": "abc"}, ["姓名", "您的微信号"])


def test_projection_drops_non_whitelisted_fields():
    out = project({"姓名": "张三", "院系": "元培学院", "骨干": "是"}, ["姓名"])
    assert out == {"姓名": "张三"}


def test_search_records_rejects_sensitive_projection():
    session = RecordingSession(handler=lambda *a, **k: ok({}))
    client = make_client(session)
    with pytest.raises(SensitiveFieldError):
        client.search_records(BASE, TABLE, field_names=["姓名", "身份证"])


# --------------------------------------------------------------------------
# 切片 5：归一化与分页
# --------------------------------------------------------------------------


def test_list_blocks_accepts_items_key():
    def handler(method: str, url: str, kwargs: dict):
        if "tenant_access_token" in url:
            return token_ok()
        return ok({"items": [{"block_id": "tbl9", "block_type": "table", "name": "B"}]})

    client = make_client(RecordingSession(handler=handler))
    blocks = client.list_blocks(BASE)

    assert blocks[0]["id"] == "tbl9"
    assert blocks[0]["type"] == "table"


def test_search_records_paginates():
    state = {"n": 0}

    def handler(method: str, url: str, kwargs: dict):
        if "tenant_access_token" in url:
            return token_ok()
        state["n"] += 1
        if state["n"] == 1:
            return ok({"records": [{"record_id": "r1", "fields": {}}], "has_more": True})
        return ok({"records": [{"record_id": "r2", "fields": {}}], "has_more": False})

    client = make_client(RecordingSession(handler=handler))
    rows = client.search_records(BASE, TABLE, keyword="张三", search_fields=["姓名"], limit=1)

    assert [r["record_id"] for r in rows] == ["r1", "r2"]


def test_missing_credentials_rejected():
    with pytest.raises(ValueError):
        FeishuHttpClient("", "", session=RecordingSession())


def test_error_body_is_surfaced():
    def handler(method: str, url: str, kwargs: dict):
        if "tenant_access_token" in url:
            return token_ok()
        return err(1254005, "table not found", status=200)

    client = make_client(RecordingSession(handler=handler))
    with pytest.raises(FeishuError) as info:
        client.list_blocks(BASE)

    assert info.value.code == 1254005
