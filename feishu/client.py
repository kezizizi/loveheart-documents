"""飞书多维表格 HTTP 客户端。

设计要点
--------
* 优先使用新版 ``/open-apis/base/v3/bases/...`` 接口（能拿到文件夹树）。
* 若新版接口不可用（权限或版本问题），自动回退到旧版 ``/bitable/v1/apps/...``
  的字段与记录接口；文件夹树无旧版对应，回退时降级为「拿不到部组」。
* token 进程内单例复用，提前 5 分钟续期，并发首取加锁。
* 只对 429 / 5xx 做指数退避重试；403 等权限错误不重试，直接抛出。
* **隐私守卫**：所有出站字段名与入站字段名都强制过一遍敏感字段断言。
"""

from __future__ import annotations

import random
import threading
import time
from typing import Any, Callable, Protocol, Sequence

from config import (
    DEFAULT_MAX_WORKERS,
    DEFAULT_TIMEOUT,
    SENSITIVE_FIELDS,
)
from feishu import schema as S

DEFAULT_BASE_URL = "https://open.feishu.cn/open-apis"

# 视为「新版接口不可用」的错误码 / HTTP 状态，触发回退
_FALLBACK_CODES = {
    99991672,  # 未开通对应 scope
    99991663,  # 无权限
    1061004,  # 新版接口不存在
    1061045,
    1254005,
}
_FALLBACK_STATUS = {403, 404, 405, 501}


class FeishuError(RuntimeError):
    """飞书接口返回的业务错误。"""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        status: int | None = None,
        path: str | None = None,
    ) -> None:
        self.code = code
        self.status = status
        self.path = path
        detail = message
        if code is not None:
            detail += f"（code={code}）"
        if status is not None:
            detail += f"，HTTP {status}"
        if path:
            detail += f"，接口 {path}"
        super().__init__(detail)


class SensitiveFieldError(RuntimeError):
    """敏感字段越界。属于代码缺陷，必须立即失败。"""


class _Session(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> Any: ...


# --------------------------------------------------------------------------
# 隐私守卫
# --------------------------------------------------------------------------


def assert_names_allowed(names: Sequence[str] | None) -> None:
    """出站字段名不得包含敏感字段。"""
    if not names:
        return
    hit = SENSITIVE_FIELDS.intersection(names)
    if hit:
        raise SensitiveFieldError(f"请求字段命中敏感名单：{sorted(hit)}")


def assert_fields_allowed(fields: dict[str, Any]) -> None:
    """入站记录字段不得包含敏感字段。"""
    hit = SENSITIVE_FIELDS.intersection(fields.keys())
    if hit:
        raise SensitiveFieldError(f"返回数据命中敏感名单：{sorted(hit)}")


def project(fields: dict[str, Any], allow: Sequence[str] | None) -> dict[str, Any]:
    """按白名单投影，随后做敏感字段断言。"""
    if allow is None:
        out = dict(fields)
    else:
        allow_set = set(allow)
        out = {k: v for k, v in fields.items() if k in allow_set}
    assert_fields_allowed(out)
    return out


# --------------------------------------------------------------------------
# 客户端
# --------------------------------------------------------------------------


class FeishuHttpClient:
    """实现 :class:`feishu.protocol.BitableClient`。"""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        session: _Session | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = 4,
        max_workers: int = DEFAULT_MAX_WORKERS,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        if not app_id or not app_secret:
            raise ValueError("缺少飞书应用凭证（app_id / app_secret）")

        self._app_id = app_id
        self._app_secret = app_secret
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self.max_workers = max_workers
        self._clock = clock
        self._sleep = sleeper
        self._rng = rng or random.Random()

        self._session: _Session = session or _default_session()

        self._token: str | None = None
        self._token_expire_at = 0.0
        self._token_lock = threading.Lock()

        # None = 尚未探测；3 / 1 = 已确定可用的接口版本
        self._api_version: int | None = None

    # ---------------------------------------------------------------- 认证

    def _get_token(self) -> str:
        with self._token_lock:
            now = self._clock()
            if self._token and now < self._token_expire_at - 300:
                return self._token

            payload = {"app_id": self._app_id, "app_secret": self._app_secret}
            resp = self._session.request(
                "POST",
                f"{self._base_url}/auth/v3/tenant_access_token/internal",
                json=payload,
                timeout=self._timeout,
            )
            body = _json(resp)
            if body.get("code") not in (0, None):
                raise FeishuError(
                    f"获取 tenant_access_token 失败：{body.get('msg')}",
                    code=body.get("code"),
                    status=getattr(resp, "status_code", None),
                )
            token = body.get("tenant_access_token")
            if not token:
                raise FeishuError("获取 tenant_access_token 失败：响应中没有 token")

            self._token = token
            expire = _safe_int(body.get("expire")) or 7200
            self._token_expire_at = now + expire
            return token

    # ---------------------------------------------------------------- 传输

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            token = self._get_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            }
            try:
                resp = self._session.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_body,
                    timeout=self._timeout,
                )
            except Exception as exc:  # 网络层异常 → 视为可重试
                last_error = exc
                if attempt >= self._max_retries:
                    raise FeishuError(f"请求失败：{exc}", path=path) from exc
                self._backoff(attempt, None)
                continue

            status = getattr(resp, "status_code", 200)
            body = _json(resp)

            if status != 200 or body.get("code") not in (0, None):
                code = body.get("code")
                msg = body.get("msg") or f"HTTP {status}"
                err = FeishuError(msg, code=code, status=status, path=path)

                if status == 429 or 500 <= status < 600:
                    last_error = err
                    if attempt >= self._max_retries:
                        raise err
                    self._backoff(attempt, _retry_after(resp))
                    continue

                if (
                    code in _FALLBACK_CODES
                    or status in _FALLBACK_STATUS
                ) and self._api_version != 1:
                    # 新版接口不可用 → 打标记，交由调用方回退
                    self._api_version = 1
                raise err

            data = body.get("data")
            return data if isinstance(data, dict) else {}

        raise FeishuError(f"请求失败：{last_error}", path=path)

    def _backoff(self, attempt: int, retry_after: float | None) -> None:
        if retry_after is not None and retry_after > 0:
            self._sleep(min(retry_after, 10.0))
            return
        base = 0.5 * (2**attempt)
        jitter = self._rng.uniform(0, 0.3)
        self._sleep(min(base + jitter, 8.0))

    # ------------------------------------------------------------ v3 / v1

    def _v3(self) -> bool:
        return self._api_version != 1

    def _try_v3_then_v1(
        self,
        v3_fn: Callable[[], dict[str, Any]],
        v1_fn: Callable[[], dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """先试新版，失败且存在旧版实现时回退。"""
        if self._api_version == 1 and v1_fn is not None:
            return v1_fn()

        if self._api_version == 3 or self._api_version is None:
            try:
                data = v3_fn()
                self._api_version = 3
                return data
            except FeishuError:
                # 新版接口失败：只有当 _request 已经把版本标记为 1
                # （说明属于「新版不可用」类错误）时才允许回退。
                if v1_fn is None or self._api_version != 1:
                    raise

        if v1_fn is None:
            raise FeishuError("该接口没有可用的旧版回退实现")
        return v1_fn()

    # ---------------------------------------------------------------- Block

    def list_blocks(
        self,
        base_token: str,
        *,
        parent_id: str | None = None,
        block_type: str | None = None,
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {}
        if parent_id:
            body["parent_id"] = parent_id
        if block_type:
            body["type"] = block_type

        data = self._request(
            "POST", f"/base/v3/bases/{base_token}/blocks/list", json_body=body
        )
        return S.normalize_blocks(data)

    # ---------------------------------------------------------------- Field

    def list_fields(self, base_token: str, table_id: str) -> list[dict[str, Any]]:
        def _v3() -> dict[str, Any]:
            out: list[dict[str, Any]] = []
            offset = 0
            while True:
                data = self._request(
                    "GET",
                    f"/base/v3/bases/{base_token}/tables/{table_id}/fields",
                    params={"limit": 300, "offset": offset},
                )
                batch = data.get("fields") or data.get("items") or []
                out.extend(batch)
                total = _safe_int(data.get("total"))
                offset += len(batch)
                if not batch or (total is not None and offset >= total):
                    break
                if len(batch) < 300:
                    break
            return {"items": out}

        def _v1() -> dict[str, Any]:
            out: list[dict[str, Any]] = []
            page_token = ""
            while True:
                params = {"page_size": 200}
                if page_token:
                    params["page_token"] = page_token
                data = self._request(
                    "GET",
                    f"/bitable/v1/apps/{base_token}/tables/{table_id}/fields",
                    params=params,
                )
                batch = data.get("items") or []
                out.extend(batch)
                if not data.get("has_more"):
                    break
                page_token = data.get("page_token") or ""
                if not page_token:
                    break
            return {"items": out}

        data = self._try_v3_then_v1(_v3, _v1)
        return S.normalize_fields(data.get("items") or data)

    # --------------------------------------------------------------- Record

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
        allow = list(field_names) if field_names else None
        assert_names_allowed(allow)

        def _payload(offset: int) -> dict[str, Any]:
            body: dict[str, Any] = {"limit": limit, "offset": offset}
            if keyword:
                body["keyword"] = keyword
            if search_fields:
                body["search_fields"] = list(search_fields)
            if allow:
                body["field_names"] = allow
            if automatic_fields:
                body["automatic_fields"] = True
            return body

        def _v3() -> dict[str, Any]:
            out: list[dict[str, Any]] = []
            offset = 0
            while True:
                data = self._request(
                    "POST",
                    f"/base/v3/bases/{base_token}/tables/{table_id}/records/search",
                    json_body=_payload(offset),
                )
                batch = data.get("records") or data.get("items") or []
                out.extend(batch)
                total = _safe_int(data.get("total"))
                offset += len(batch)
                if not batch or not data.get("has_more", False):
                    if total is None or offset >= total:
                        break
                if len(batch) < limit:
                    break
            return {"items": out}

        def _v1() -> dict[str, Any]:
            out: list[dict[str, Any]] = []
            page_token = ""
            while True:
                body: dict[str, Any] = {}
                if keyword:
                    body["keyword"] = keyword
                if search_fields:
                    body["search_fields"] = list(search_fields)
                if allow:
                    body["field_names"] = allow
                if automatic_fields:
                    body["automatic_fields"] = True
                params = {"page_size": 500}
                if page_token:
                    params["page_token"] = page_token
                data = self._request(
                    "POST",
                    f"/bitable/v1/apps/{base_token}/tables/{table_id}/records/search",
                    params=params,
                    json_body=body,
                )
                batch = data.get("items") or []
                out.extend(batch)
                if not data.get("has_more"):
                    break
                page_token = data.get("page_token") or ""
                if not page_token:
                    break
            return {"items": out}

        data = self._try_v3_then_v1(_v3, _v1)
        return S.normalize_records(data.get("items") or data)

    def batch_get_records(
        self,
        base_token: str,
        table_id: str,
        record_ids: Sequence[str],
        *,
        field_names: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        allow = list(field_names) if field_names else None
        assert_names_allowed(allow)
        ids = [r for r in record_ids if r]
        if not ids:
            return []

        out: list[dict[str, Any]] = []
        for chunk in _chunks(ids, 100):
            body: dict[str, Any] = {"record_ids": list(chunk)}
            if allow:
                body["field_names"] = allow
            data = self._request(
                "POST",
                f"/base/v3/bases/{base_token}/tables/{table_id}/records/batch_get",
                json_body=body,
            )
            out.extend(data.get("records") or data.get("items") or [])
        return S.normalize_records(out)


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------


def _default_session() -> _Session:
    import requests

    return requests.Session()


def _json(resp: Any) -> dict[str, Any]:
    try:
        body = resp.json()
    except Exception as exc:  # pragma: no cover - 非 JSON 响应
        raise FeishuError(f"响应不是合法 JSON：{exc}") from exc
    return body if isinstance(body, dict) else {}


def _retry_after(resp: Any) -> float | None:
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    try:
        raw = headers.get("Retry-After") or headers.get("retry-after")
    except Exception:
        return None
    return _safe_float(raw)


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _chunks(seq: Sequence[str], size: int):  # type: ignore[no-untyped-def]
    for i in range(0, len(seq), size):
        yield seq[i : i + size]
