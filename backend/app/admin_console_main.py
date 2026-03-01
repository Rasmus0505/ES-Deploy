from __future__ import annotations

import csv
import io
import json
import os
import secrets
import threading
from dataclasses import dataclass
from time import time
from typing import Any

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from app.oneapi_client import OneAPIClient, OneAPIClientError


def _now_ms() -> int:
    return int(round(time() * 1000))


def _safe_positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return fallback
    return parsed if parsed > 0 else fallback


def _safe_non_negative_int(value: Any, fallback: int = 0) -> int:
    try:
        parsed = int(value)
    except Exception:
        return fallback
    return parsed if parsed >= 0 else fallback


def _safe_non_negative_float(value: Any, fallback: float = 0.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        return fallback
    return parsed if parsed >= 0 else fallback


def _normalize_base_url(raw: str, fallback: str) -> str:
    safe = str(raw or "").strip()
    if not safe:
        safe = fallback
    return safe.rstrip("/")


def _extract_role_label(payload: dict[str, Any]) -> str:
    role = payload.get("role")
    if isinstance(role, str):
        return role.strip().lower()
    if isinstance(role, (int, float)):
        return str(int(role))
    if payload.get("is_admin") is True:
        return "admin"
    return ""


def _is_admin_user(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    if bool(payload.get("is_admin") or payload.get("admin")):
        return True
    role = payload.get("role")
    if isinstance(role, str):
        safe = role.strip().lower()
        if safe in {"admin", "administrator", "root", "super_admin", "superadmin"}:
            return True
    if isinstance(role, (int, float)):
        # OneAPI 常见实现中管理员 role 通常高于普通用户
        return int(role) >= 10
    return False


@dataclass
class AdminSession:
    session_token: str
    oneapi_access_token: str
    user_id: str
    username: str
    role_label: str
    created_at: int
    expires_at: int


class AdminSessionStore:
    def __init__(self, ttl_hours: int = 12) -> None:
        self._ttl_ms = max(1, int(ttl_hours or 12)) * 3600 * 1000
        self._lock = threading.RLock()
        self._sessions: dict[str, AdminSession] = {}

    def create(
        self, *, oneapi_access_token: str, user_payload: dict[str, Any]
    ) -> AdminSession:
        now = _now_ms()
        token = secrets.token_urlsafe(32)
        session = AdminSession(
            session_token=token,
            oneapi_access_token=str(oneapi_access_token or "").strip(),
            user_id=str(
                user_payload.get("id") or user_payload.get("user_id") or ""
            ).strip(),
            username=str(user_payload.get("username") or "").strip(),
            role_label=_extract_role_label(user_payload),
            created_at=now,
            expires_at=now + self._ttl_ms,
        )
        with self._lock:
            self._sessions[token] = session
        return session

    def get(self, token: str) -> AdminSession | None:
        safe = str(token or "").strip()
        if not safe:
            return None
        now = _now_ms()
        with self._lock:
            session = self._sessions.get(safe)
            if session is None:
                return None
            if session.expires_at <= now:
                self._sessions.pop(safe, None)
                return None
            return session

    def revoke(self, token: str) -> None:
        safe = str(token or "").strip()
        if not safe:
            return
        with self._lock:
            self._sessions.pop(safe, None)


ONEAPI_CLIENT = OneAPIClient()
USER_BACKEND_API_BASE = _normalize_base_url(
    os.getenv("USER_BACKEND_API_BASE", "http://127.0.0.1:8766/api/v1"),
    fallback="http://127.0.0.1:8766/api/v1",
)
ASR_ADMIN_SERVICE_TOKEN = str(os.getenv("ASR_ADMIN_SERVICE_TOKEN", "")).strip()
ADMIN_SESSION_STORE = AdminSessionStore(
    ttl_hours=_safe_positive_int(os.getenv("ADMIN_SESSION_TTL_HOURS", "12"), 12)
)


app = FastAPI(title="ASR Admin Console", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _admin_http_error(code: int, message: str) -> HTTPException:
    return HTTPException(
        status_code=code, detail={"code": "admin_error", "message": message}
    )


def _extract_bearer_token(request: Request) -> str:
    auth_header = str(request.headers.get("authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return ""


def _require_admin_session(request: Request) -> AdminSession:
    token = _extract_bearer_token(request)
    if not token:
        raise HTTPException(
            status_code=401,
            detail={"code": "missing_admin_token", "message": "缺少管理员令牌"},
        )
    session = ADMIN_SESSION_STORE.get(token)
    if session is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "invalid_admin_token",
                "message": "管理员登录态已失效，请重新登录",
            },
        )
    return session


def _call_user_backend(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    actor: str = "",
) -> dict[str, Any]:
    if not ASR_ADMIN_SERVICE_TOKEN:
        raise _admin_http_error(
            503, "ASR_ADMIN_SERVICE_TOKEN 未配置，无法调用用户后端内部接口"
        )
    safe_path = str(path or "").strip()
    if not safe_path.startswith("/"):
        safe_path = f"/{safe_path}"
    url = f"{USER_BACKEND_API_BASE}{safe_path}"
    headers = {
        "Accept": "application/json",
        "X-ASR-ADMIN-SERVICE-TOKEN": ASR_ADMIN_SERVICE_TOKEN,
    }
    safe_actor = str(actor or "").strip()
    if safe_actor:
        headers["X-ASR-ADMIN-ACTOR"] = safe_actor
    try:
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=json_body,
            timeout=20,
        )
    except requests.RequestException as exc:
        raise _admin_http_error(502, f"调用用户后端失败: {exc}") from exc
    try:
        payload: Any = response.json()
    except Exception:
        payload = {"message": str(response.text or "")[:500]}
    if response.status_code >= 400:
        message = ""
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, dict):
                message = str(detail.get("message") or detail.get("code") or "").strip()
            if not message:
                message = str(payload.get("message") or "").strip()
        if not message:
            message = f"用户后端返回错误 HTTP {response.status_code}"
        raise _admin_http_error(response.status_code, message)
    if isinstance(payload, dict):
        return payload
    return {"status": "ok", "data": payload}


def _merge_user_list_with_usage(
    *, users: list[dict[str, Any]], usage: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    usage_by_user_id: dict[str, dict[str, Any]] = {}
    for item in usage:
        if not isinstance(item, dict):
            continue
        user_id = str(item.get("user_id") or "").strip()
        if not user_id:
            continue
        usage_by_user_id[user_id] = item

    merged: list[dict[str, Any]] = []
    seen_user_ids: set[str] = set()
    for user in users:
        if not isinstance(user, dict):
            continue
        user_id = str(user.get("id") or user.get("user_id") or "").strip()
        username = str(user.get("username") or "").strip()
        if not user_id:
            continue
        seen_user_ids.add(user_id)
        stats = usage_by_user_id.get(user_id) or {}
        quota = _safe_non_negative_int(user.get("quota"), 0)
        used_quota = _safe_non_negative_int(user.get("used_quota"), 0)
        merged.append(
            {
                "user_id": user_id,
                "username": username,
                "quota": quota,
                "used_quota": used_quota,
                "remaining_quota": max(0, quota - used_quota),
                "request_count": _safe_non_negative_int(user.get("request_count"), 0),
                "role": user.get("role"),
                "status": user.get("status"),
                "asr_billed_quota": _safe_non_negative_int(
                    stats.get("billed_quota"), 0
                ),
                "asr_charge_count": _safe_non_negative_int(
                    stats.get("charge_count"), 0
                ),
                "asr_billed_seconds": _safe_non_negative_float(
                    stats.get("billed_seconds"), 0.0
                ),
                "asr_base_cost_cny": _safe_non_negative_float(
                    stats.get("base_cost_cny"), 0.0
                ),
                "asr_billed_cost_cny": _safe_non_negative_float(
                    stats.get("billed_cost_cny"), 0.0
                ),
                "asr_last_charged_at": _safe_non_negative_int(
                    stats.get("last_charged_at"), 0
                ),
            }
        )

    for user_id, stats in usage_by_user_id.items():
        if user_id in seen_user_ids:
            continue
        merged.append(
            {
                "user_id": user_id,
                "username": "",
                "quota": 0,
                "used_quota": 0,
                "remaining_quota": 0,
                "request_count": 0,
                "role": None,
                "status": None,
                "asr_billed_quota": _safe_non_negative_int(
                    stats.get("billed_quota"), 0
                ),
                "asr_charge_count": _safe_non_negative_int(
                    stats.get("charge_count"), 0
                ),
                "asr_billed_seconds": _safe_non_negative_float(
                    stats.get("billed_seconds"), 0.0
                ),
                "asr_base_cost_cny": _safe_non_negative_float(
                    stats.get("base_cost_cny"), 0.0
                ),
                "asr_billed_cost_cny": _safe_non_negative_float(
                    stats.get("billed_cost_cny"), 0.0
                ),
                "asr_last_charged_at": _safe_non_negative_int(
                    stats.get("last_charged_at"), 0
                ),
            }
        )

    return merged


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "asr-admin-console",
        "user_backend_api_base": USER_BACKEND_API_BASE,
        "internal_token_configured": bool(ASR_ADMIN_SERVICE_TOKEN),
    }


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _ADMIN_CONSOLE_HTML


@app.post("/api/v1/admin/auth/login")
async def admin_login(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    safe_payload = payload if isinstance(payload, dict) else {}
    username = str(safe_payload.get("username") or "").strip()
    password = str(safe_payload.get("password") or "")
    if not username or not password:
        raise _admin_http_error(400, "username 和 password 不能为空")

    try:
        login_payload = ONEAPI_CLIENT.login_admin(username=username, password=password)
    except OneAPIClientError as exc:
        raise _admin_http_error(
            max(400, int(exc.status_code or 400)), str(exc.message or "OneAPI 登录失败")
        ) from exc

    oneapi_access_token = str(login_payload.get("access_token") or "").strip()
    raw_user_payload = login_payload.get("user")
    user_payload: dict[str, Any] = (
        raw_user_payload if isinstance(raw_user_payload, dict) else {}
    )
    if not _is_admin_user(user_payload):
        raise HTTPException(
            status_code=403,
            detail={"code": "admin_required", "message": "当前 OneAPI 账号不是管理员"},
        )

    session = ADMIN_SESSION_STORE.create(
        oneapi_access_token=oneapi_access_token,
        user_payload=user_payload,
    )
    print(
        f"[DEBUG] Admin login success username={session.username} user_id={session.user_id}"
    )
    return {
        "status": "ok",
        "token_type": "bearer",
        "access_token": session.session_token,
        "expires_at": session.expires_at,
        "user": {
            "user_id": session.user_id,
            "username": session.username,
            "role": session.role_label,
        },
    }


@app.post("/api/v1/admin/auth/logout")
def admin_logout(request: Request) -> dict[str, Any]:
    token = _extract_bearer_token(request)
    if token:
        ADMIN_SESSION_STORE.revoke(token)
    return {"status": "ok"}


@app.get("/api/v1/admin/auth/me")
def admin_me(request: Request) -> dict[str, Any]:
    session = _require_admin_session(request)
    return {
        "status": "ok",
        "user": {
            "user_id": session.user_id,
            "username": session.username,
            "role": session.role_label,
            "expires_at": session.expires_at,
        },
    }


@app.get("/api/v1/admin/runtime-config")
def admin_get_runtime_config(request: Request) -> dict[str, Any]:
    session = _require_admin_session(request)
    payload = _call_user_backend(
        "GET",
        "/internal/asr-admin/runtime-config",
        actor=session.username,
    )
    return payload


@app.put("/api/v1/admin/runtime-config")
async def admin_update_runtime_config(request: Request) -> dict[str, Any]:
    session = _require_admin_session(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    payload = _call_user_backend(
        "PUT",
        "/internal/asr-admin/runtime-config",
        json_body=body if isinstance(body, dict) else {},
        actor=session.username,
    )
    return payload


@app.post("/api/v1/admin/asr/test")
async def admin_test_asr_connectivity(request: Request) -> dict[str, Any]:
    session = _require_admin_session(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    safe_body = body if isinstance(body, dict) else {}
    if not str(safe_body.get("oneapi_token") or "").strip():
        safe_body["oneapi_token"] = session.oneapi_access_token
    payload = _call_user_backend(
        "POST",
        "/internal/asr-admin/test-connectivity",
        json_body=safe_body,
        actor=session.username,
    )
    return payload


@app.get("/api/v1/admin/asr/users")
def admin_list_asr_users(
    request: Request,
    page: int = 0,
    page_size: int = 100,
    keyword: str = "",
) -> dict[str, Any]:
    session = _require_admin_session(request)
    safe_page = max(0, int(page or 0))
    safe_page_size = max(1, min(200, int(page_size or 100)))
    safe_keyword = str(keyword or "").strip()

    users: list[dict[str, Any]] = []
    reported_total = 0
    max_pages = 50
    try:
        cursor = safe_page
        pages = 0
        while pages < max_pages:
            payload = ONEAPI_CLIENT.admin_list_users(
                access_token=session.oneapi_access_token,
                page=cursor,
                page_size=safe_page_size,
                keyword=safe_keyword,
            )
            raw_items = payload.get("items")
            page_items: list[Any] = raw_items if isinstance(raw_items, list) else []
            reported_total = max(
                reported_total, _safe_non_negative_int(payload.get("total"), 0)
            )
            users.extend([item for item in page_items if isinstance(item, dict)])
            pages += 1
            if len(page_items) < safe_page_size:
                break
            cursor += 1
            if safe_page > 0:
                break
    except OneAPIClientError as exc:
        raise _admin_http_error(
            max(400, int(exc.status_code or 400)),
            str(exc.message or "读取 OneAPI 用户列表失败"),
        ) from exc

    deduped_users: list[dict[str, Any]] = []
    seen_user_ids: set[str] = set()
    for item in users:
        user_id = str(item.get("id") or item.get("user_id") or "").strip()
        if not user_id or user_id in seen_user_ids:
            continue
        seen_user_ids.add(user_id)
        deduped_users.append(item)

    usage_payload = _call_user_backend(
        "GET",
        "/internal/asr-admin/users",
        params={
            "limit": 5000,
            "offset": 0,
            "keyword": "",
        },
        actor=session.username,
    )
    raw_usage_items = usage_payload.get("items")
    usage_items: list[dict[str, Any]] = (
        [item for item in raw_usage_items if isinstance(item, dict)]
        if isinstance(raw_usage_items, list)
        else []
    )

    merged = _merge_user_list_with_usage(users=deduped_users, usage=usage_items)
    return {
        "status": "ok",
        "items": merged,
        "total": max(len(merged), reported_total),
        "page": safe_page,
        "page_size": safe_page_size,
        "keyword": safe_keyword,
    }


@app.post("/api/v1/admin/users/{user_id}/quota-adjust")
async def admin_adjust_user_quota(user_id: str, request: Request) -> dict[str, Any]:
    session = _require_admin_session(request)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    safe_payload = payload if isinstance(payload, dict) else {}
    delta_quota = int(safe_payload.get("delta_quota") or 0)
    reason = str(safe_payload.get("reason") or "").strip()
    if delta_quota == 0:
        raise _admin_http_error(400, "delta_quota 不能为 0")

    try:
        before_user = ONEAPI_CLIENT.admin_get_user(
            access_token=session.oneapi_access_token,
            user_id=user_id,
        )
    except OneAPIClientError as exc:
        raise _admin_http_error(
            max(400, int(exc.status_code or 400)), str(exc.message or "读取用户失败")
        ) from exc

    before_quota = _safe_non_negative_int(before_user.get("quota"), 0)
    new_quota = max(0, before_quota + delta_quota)
    try:
        after_user = ONEAPI_CLIENT.admin_update_user_quota(
            access_token=session.oneapi_access_token,
            user_id=str(before_user.get("id") or user_id),
            new_quota=new_quota,
        )
    except OneAPIClientError as exc:
        raise _admin_http_error(
            max(400, int(exc.status_code or 400)),
            str(exc.message or "更新用户额度失败"),
        ) from exc

    print(
        "[DEBUG] Admin quota adjust "
        f"actor={session.username} target={user_id} delta={delta_quota} "
        f"before={before_quota} after={_safe_non_negative_int(after_user.get('quota'), new_quota)} reason={reason or '-'}"
    )
    return {
        "status": "ok",
        "target_user": {
            "user_id": str(after_user.get("id") or user_id),
            "username": str(after_user.get("username") or ""),
            "before_quota": before_quota,
            "after_quota": _safe_non_negative_int(after_user.get("quota"), new_quota),
            "delta_quota": delta_quota,
        },
        "operator": {
            "user_id": session.user_id,
            "username": session.username,
        },
        "reason": reason,
        "at": _now_ms(),
    }


@app.get("/api/v1/admin/asr/charges")
def admin_list_asr_charges(
    request: Request,
    limit: int = 200,
    offset: int = 0,
    user_id: str = "",
) -> dict[str, Any]:
    session = _require_admin_session(request)
    payload = _call_user_backend(
        "GET",
        "/internal/asr-admin/charges",
        params={"limit": limit, "offset": offset, "user_id": user_id},
        actor=session.username,
    )
    return payload


@app.get("/api/v1/admin/asr/charges/export.csv")
def admin_export_asr_charges_csv(
    request: Request,
    limit: int = 1000,
    user_id: str = "",
) -> PlainTextResponse:
    session = _require_admin_session(request)
    payload = _call_user_backend(
        "GET",
        "/internal/asr-admin/charges",
        params={
            "limit": max(1, min(5000, int(limit or 1000))),
            "offset": 0,
            "user_id": user_id,
        },
        actor=session.username,
    )
    raw_items = payload.get("items")
    items: list[dict[str, Any]] = (
        [item for item in raw_items if isinstance(item, dict)]
        if isinstance(raw_items, list)
        else []
    )

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "created_at",
            "user_id",
            "job_id",
            "billed_seconds",
            "base_cost_cny",
            "multiplier",
            "billed_cost_cny",
            "billed_quota",
        ],
    )
    writer.writeheader()
    for item in items:
        writer.writerow(
            {
                "created_at": _safe_non_negative_int(item.get("created_at"), 0),
                "user_id": str(item.get("user_id") or ""),
                "job_id": str(item.get("job_id") or ""),
                "billed_seconds": f"{_safe_non_negative_float(item.get('billed_seconds'), 0.0):.6f}",
                "base_cost_cny": f"{_safe_non_negative_float(item.get('base_cost_cny'), 0.0):.8f}",
                "multiplier": f"{_safe_non_negative_float(item.get('multiplier'), 0.0):.4f}",
                "billed_cost_cny": f"{_safe_non_negative_float(item.get('billed_cost_cny'), 0.0):.8f}",
                "billed_quota": _safe_non_negative_int(item.get("billed_quota"), 0),
            }
        )
    csv_body = output.getvalue()
    output.close()
    return PlainTextResponse(
        content=csv_body,
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=asr-charges.csv",
        },
    )


@app.exception_handler(HTTPException)
async def _http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict):
        payload = detail
    else:
        payload = {"code": "http_error", "message": str(detail or "")}
    return JSONResponse(
        status_code=exc.status_code, content={"success": False, "detail": payload}
    )


_ADMIN_CONSOLE_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>ASR Admin Console</title>
  <style>
    :root {
      --bg: #f4f6f9;
      --panel: #ffffff;
      --text: #111827;
      --muted: #6b7280;
      --line: #e5e7eb;
      --brand: #0f766e;
      --danger: #b91c1c;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background: radial-gradient(circle at top right, #dbeafe 0, transparent 40%), var(--bg);
    }
    .wrap { max-width: 1280px; margin: 0 auto; padding: 20px; }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 16px;
      margin-bottom: 14px;
      box-shadow: 0 8px 24px rgba(15, 23, 42, 0.05);
    }
    h1,h2 { margin: 0 0 10px; }
    h1 { font-size: 22px; }
    h2 { font-size: 16px; }
    p { margin: 6px 0; color: var(--muted); }
    label { font-size: 13px; color: var(--muted); display: block; margin-bottom: 4px; }
    input, select, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 9px 10px;
      margin-bottom: 10px;
      font-size: 14px;
      background: #fff;
    }
    button {
      border: none;
      border-radius: 8px;
      padding: 9px 14px;
      background: var(--brand);
      color: #fff;
      font-weight: 600;
      cursor: pointer;
      margin-right: 8px;
    }
    button.secondary { background: #374151; }
    button.danger { background: var(--danger); }
    button:disabled { opacity: 0.55; cursor: not-allowed; }
    .row { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
    .row-2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .hide { display: none !important; }
    .muted { color: var(--muted); font-size: 13px; }
    .error { color: var(--danger); font-size: 13px; margin-top: 4px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { border-bottom: 1px solid var(--line); text-align: left; padding: 8px 6px; }
    th { color: var(--muted); font-weight: 600; }
    .toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
    .pill { display: inline-block; padding: 4px 10px; border-radius: 999px; background: #ecfeff; color: #0f766e; font-size: 12px; }
    @media (max-width: 960px) {
      .row, .row-2 { grid-template-columns: 1fr; }
      .wrap { padding: 12px; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div id="loginCard" class="card">
      <h1>ASR 管理后台</h1>
      <p>使用 OneAPI 管理员账号登录。此服务仅面向管理员。</p>
      <div class="row-2">
        <div>
          <label for="loginUsername">OneAPI 用户名</label>
          <input id="loginUsername" autocomplete="username">
        </div>
        <div>
          <label for="loginPassword">OneAPI 密码</label>
          <input id="loginPassword" type="password" autocomplete="current-password">
        </div>
      </div>
      <button id="loginBtn">登录后台</button>
      <div id="loginError" class="error"></div>
    </div>

    <div id="console" class="hide">
      <div class="card">
        <div class="toolbar">
          <h1 style="margin-right:auto">全局 ASR 管理台</h1>
          <span id="whoami" class="pill">未登录</span>
          <button id="logoutBtn" class="secondary">退出登录</button>
        </div>
        <p>说明：此后台默认走 OneAPI 管理员鉴权；调账直接写 OneAPI 用户额度；倍率和路由配置实时生效。</p>
      </div>

      <div class="card">
        <h2>路由与倍率配置（实时）</h2>
        <div class="row">
          <div>
            <label for="cfgRouteMode">路由模式</label>
            <select id="cfgRouteMode">
              <option value="dashscope_direct">dashscope_direct</option>
              <option value="oneapi_fallback">oneapi_fallback</option>
            </select>
          </div>
          <div>
            <label for="cfgDashscopeBase">DashScope Base URL</label>
            <input id="cfgDashscopeBase">
          </div>
          <div>
            <label for="cfgGlobalMultiplier">全局倍率</label>
            <input id="cfgGlobalMultiplier" type="number" step="0.01" min="0">
          </div>
          <div>
            <label for="cfgSubmitMin">提交最低剩余额度</label>
            <input id="cfgSubmitMin" type="number" step="1" min="1">
          </div>
        </div>
        <div class="row">
          <div>
            <label>paraformer-v2 启用</label>
            <select id="cfgEnableParaformer"><option value="true">true</option><option value="false">false</option></select>
          </div>
          <div>
            <label>paraformer-v2 倍率</label>
            <input id="cfgMulParaformer" type="number" step="0.01" min="0">
          </div>
          <div>
            <label>qwen3-asr-flash-filetrans 启用</label>
            <select id="cfgEnableQwen"><option value="true">true</option><option value="false">false</option></select>
          </div>
          <div>
            <label>qwen3-asr-flash-filetrans 倍率</label>
            <input id="cfgMulQwen" type="number" step="0.01" min="0">
          </div>
        </div>
        <div class="row-2">
          <div>
            <label for="cfgNote">备注（可选）</label>
            <input id="cfgNote">
          </div>
          <div>
            <label>API Key 状态</label>
            <input id="cfgKeyStatus" disabled>
          </div>
        </div>
        <div class="row-2">
          <div>
            <label for="cfgDashscopeApiKey">DashScope API Key（留空则不修改）</label>
            <input id="cfgDashscopeApiKey" type="password" autocomplete="off" placeholder="sk-...">
          </div>
          <div>
            <label>密钥来源</label>
            <input id="cfgKeySourceHint" disabled>
          </div>
        </div>
        <button id="saveConfigBtn">保存配置</button>
        <button id="reloadConfigBtn" class="secondary">刷新配置</button>
        <button id="clearKeyBtn" class="danger">清空后台密钥</button>
        <div id="cfgError" class="error"></div>
      </div>

      <div class="card">
        <h2>ASR 连通测试</h2>
        <p>默认按当前路由配置测试；若路由为 oneapi_fallback，可填 OneAPI Token 覆盖自动令牌。</p>
        <div class="row">
          <div>
            <label for="testModel">测试模型</label>
            <select id="testModel">
              <option value="paraformer-v2">paraformer-v2</option>
              <option value="qwen3-asr-flash-filetrans">qwen3-asr-flash-filetrans</option>
            </select>
          </div>
          <div>
            <label for="testLanguage">语言</label>
            <input id="testLanguage" value="en">
          </div>
          <div>
            <label for="testOneapiToken">OneAPI Token（可选）</label>
            <input id="testOneapiToken" type="password" autocomplete="off" placeholder="sk-...">
          </div>
          <div>
            <label>操作</label>
            <button id="runTestBtn">一键测试</button>
          </div>
        </div>
        <textarea id="testResult" rows="6" readonly style="width:100%;margin-top:8px"></textarea>
        <div id="testError" class="error"></div>
      </div>

      <div class="card">
        <h2>全局用户列表（OneAPI + ASR 合并）</h2>
        <div class="toolbar">
          <input id="userKeyword" placeholder="按用户ID/用户名搜索" style="max-width:320px">
          <button id="reloadUsersBtn" class="secondary">刷新列表</button>
        </div>
        <table>
          <thead>
            <tr>
              <th>用户</th><th>OneAPI余额</th><th>OneAPI已用</th><th>ASR已扣</th><th>ASR次数</th><th>操作</th>
            </tr>
          </thead>
          <tbody id="usersBody"></tbody>
        </table>
        <div id="usersError" class="error"></div>
      </div>

      <div class="card">
        <h2>全局 ASR 流水</h2>
        <div class="toolbar">
          <input id="chargeUserId" placeholder="按 user_id 过滤" style="max-width:260px">
          <button id="reloadChargesBtn" class="secondary">刷新流水</button>
          <a id="exportLink" href="#" target="_blank"><button class="secondary" type="button">导出CSV</button></a>
        </div>
        <table>
          <thead>
            <tr>
              <th>时间</th><th>用户ID</th><th>任务ID</th><th>秒数</th><th>基础成本</th><th>倍率</th><th>倍率后成本</th><th>额度</th>
            </tr>
          </thead>
          <tbody id="chargesBody"></tbody>
        </table>
        <div id="chargesError" class="error"></div>
      </div>
    </div>
  </div>

  <script>
    const state = { token: '', me: null, users: [] };

    const byId = (id) => document.getElementById(id);
    const asBool = (value) => String(value) === 'true';
    const fmt = (v) => Number(v || 0).toLocaleString('zh-CN');
    const fmtTime = (v) => {
      const n = Number(v || 0);
      if (!n) return '-';
      const d = new Date(n);
      if (Number.isNaN(d.getTime())) return '-';
      return d.toLocaleString('zh-CN', { hour12: false });
    };

    function setError(id, message) {
      byId(id).textContent = String(message || '');
    }

    async function api(path, options = {}) {
      const headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
      if (state.token) headers.Authorization = `Bearer ${state.token}`;
      const response = await fetch(path, Object.assign({}, options, { headers }));
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = payload && payload.detail ? payload.detail : {};
        const message = detail.message || payload.message || `HTTP ${response.status}`;
        throw new Error(message);
      }
      return payload;
    }

    async function login() {
      setError('loginError', '');
      const username = byId('loginUsername').value.trim();
      const password = byId('loginPassword').value;
      if (!username || !password) {
        setError('loginError', '请输入用户名和密码');
        return;
      }
      try {
        const payload = await api('/api/v1/admin/auth/login', {
          method: 'POST',
          body: JSON.stringify({ username, password })
        });
        state.token = payload.access_token || '';
        state.me = payload.user || null;
        byId('loginCard').classList.add('hide');
        byId('console').classList.remove('hide');
        byId('whoami').textContent = `${state.me?.username || '-'} (${state.me?.role || '-'})`;
        await Promise.all([loadConfig(), loadUsers(), loadCharges()]);
      } catch (err) {
        setError('loginError', err.message || '登录失败');
      }
    }

    async function logout() {
      try { await api('/api/v1/admin/auth/logout', { method: 'POST' }); } catch {}
      state.token = '';
      state.me = null;
      byId('console').classList.add('hide');
      byId('loginCard').classList.remove('hide');
    }

    async function loadConfig() {
      setError('cfgError', '');
      try {
        const payload = await api('/api/v1/admin/runtime-config');
        const cfg = payload.config || {};
        byId('cfgRouteMode').value = cfg.route_mode || 'oneapi_fallback';
        byId('cfgDashscopeBase').value = cfg.dashscope_base_url || '';
        byId('cfgGlobalMultiplier').value = Number(cfg.global_multiplier || 0);
        byId('cfgSubmitMin').value = Number(cfg.submit_min_remaining_quota || 1);
        const enabled = cfg.model_enabled || {};
        const multipliers = cfg.model_multipliers || {};
        byId('cfgEnableParaformer').value = String(Boolean(enabled['paraformer-v2']) !== false);
        byId('cfgEnableQwen').value = String(Boolean(enabled['qwen3-asr-flash-filetrans']) !== false);
        byId('cfgMulParaformer').value = Number(multipliers['paraformer-v2'] ?? cfg.global_multiplier ?? 0);
        byId('cfgMulQwen').value = Number(multipliers['qwen3-asr-flash-filetrans'] ?? cfg.global_multiplier ?? 0);
        byId('cfgKeyStatus').value = cfg.api_key_configured ? `已配置 (${cfg.api_key_masked || '***'})` : '未配置';
        byId('cfgKeySourceHint').value = cfg.api_key_source || 'none';
        byId('cfgDashscopeApiKey').value = '';
      } catch (err) {
        setError('cfgError', err.message || '读取配置失败');
      }
    }

    async function saveConfig() {
      setError('cfgError', '');
      try {
        const body = {
          route_mode: byId('cfgRouteMode').value,
          dashscope_base_url: byId('cfgDashscopeBase').value.trim(),
          global_multiplier: Number(byId('cfgGlobalMultiplier').value || 0),
          submit_min_remaining_quota: Number(byId('cfgSubmitMin').value || 1),
          model_enabled: {
            'paraformer-v2': asBool(byId('cfgEnableParaformer').value),
            'qwen3-asr-flash-filetrans': asBool(byId('cfgEnableQwen').value)
          },
          model_multipliers: {
            'paraformer-v2': Number(byId('cfgMulParaformer').value || 0),
            'qwen3-asr-flash-filetrans': Number(byId('cfgMulQwen').value || 0)
          },
          note: byId('cfgNote').value.trim()
        };
        const dashscopeApiKey = byId('cfgDashscopeApiKey').value.trim();
        if (dashscopeApiKey) {
          body.dashscope_api_key = dashscopeApiKey;
        }
        await api('/api/v1/admin/runtime-config', { method: 'PUT', body: JSON.stringify(body) });
        await loadConfig();
      } catch (err) {
        setError('cfgError', err.message || '保存配置失败');
      }
    }

    async function clearDashscopeApiKey() {
      setError('cfgError', '');
      try {
        await api('/api/v1/admin/runtime-config', {
          method: 'PUT',
          body: JSON.stringify({
            clear_dashscope_api_key: true,
            note: 'admin_clear_dashscope_key'
          })
        });
        byId('cfgDashscopeApiKey').value = '';
        await loadConfig();
      } catch (err) {
        setError('cfgError', err.message || '清空密钥失败');
      }
    }

    async function runAsrTest() {
      setError('testError', '');
      byId('testResult').value = '测试中...';
      try {
        const body = {
          model: byId('testModel').value,
          language: byId('testLanguage').value.trim() || 'en'
        };
        const oneapiToken = byId('testOneapiToken').value.trim();
        if (oneapiToken) {
          body.oneapi_token = oneapiToken;
        }
        const payload = await api('/api/v1/admin/asr/test', {
          method: 'POST',
          body: JSON.stringify(body)
        });
        byId('testResult').value = JSON.stringify(payload, null, 2);
      } catch (err) {
        byId('testResult').value = '';
        setError('testError', err.message || 'ASR 连通测试失败');
      }
    }

    function renderUsers(items) {
      const body = byId('usersBody');
      body.innerHTML = '';
      if (!Array.isArray(items) || items.length === 0) {
        const tr = document.createElement('tr');
        tr.innerHTML = '<td colspan="6" class="muted">暂无数据</td>';
        body.appendChild(tr);
        return;
      }
      for (const item of items) {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td>${item.user_id || '-'}<br><span class="muted">${item.username || '-'}</span></td>
          <td>${fmt(item.quota)}</td>
          <td>${fmt(item.used_quota)}</td>
          <td>${fmt(item.asr_billed_quota)}</td>
          <td>${fmt(item.asr_charge_count)}</td>
          <td>
            <button data-action="adjust" data-user="${item.user_id}">调账</button>
          </td>
        `;
        body.appendChild(tr);
      }
      body.querySelectorAll('button[data-action="adjust"]').forEach((button) => {
        button.addEventListener('click', async () => {
          const userId = button.getAttribute('data-user') || '';
          const deltaRaw = prompt(`输入调账额度（正数增加，负数扣减）\nuser_id=${userId}`, '10000');
          if (deltaRaw === null) return;
          const delta = Number(deltaRaw || 0);
          if (!Number.isFinite(delta) || !delta) {
            alert('请输入有效的非零整数');
            return;
          }
          const reason = prompt('请输入调账原因（可选）', '') || '';
          try {
            await api(`/api/v1/admin/users/${encodeURIComponent(userId)}/quota-adjust`, {
              method: 'POST',
              body: JSON.stringify({ delta_quota: Math.trunc(delta), reason })
            });
            await loadUsers();
          } catch (err) {
            alert(err.message || '调账失败');
          }
        });
      });
    }

    async function loadUsers() {
      setError('usersError', '');
      try {
        const keyword = byId('userKeyword').value.trim();
        const payload = await api(`/api/v1/admin/asr/users?page=0&page_size=200&keyword=${encodeURIComponent(keyword)}`);
        state.users = payload.items || [];
        renderUsers(state.users);
      } catch (err) {
        setError('usersError', err.message || '读取用户列表失败');
      }
    }

    function renderCharges(items) {
      const body = byId('chargesBody');
      body.innerHTML = '';
      if (!Array.isArray(items) || items.length === 0) {
        const tr = document.createElement('tr');
        tr.innerHTML = '<td colspan="8" class="muted">暂无流水</td>';
        body.appendChild(tr);
        return;
      }
      for (const item of items) {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td>${fmtTime(item.created_at)}</td>
          <td>${item.user_id || '-'}</td>
          <td>${item.job_id || '-'}</td>
          <td>${Number(item.billed_seconds || 0).toFixed(3)}</td>
          <td>${Number(item.base_cost_cny || 0).toFixed(8)}</td>
          <td>${Number(item.multiplier || 0).toFixed(4)}</td>
          <td>${Number(item.billed_cost_cny || 0).toFixed(8)}</td>
          <td>${fmt(item.billed_quota)}</td>
        `;
        body.appendChild(tr);
      }
    }

    async function loadCharges() {
      setError('chargesError', '');
      try {
        const userId = byId('chargeUserId').value.trim();
        const payload = await api(`/api/v1/admin/asr/charges?limit=500&offset=0&user_id=${encodeURIComponent(userId)}`);
        renderCharges(payload.items || []);
        byId('exportLink').href = `/api/v1/admin/asr/charges/export.csv?limit=5000&user_id=${encodeURIComponent(userId)}`;
      } catch (err) {
        setError('chargesError', err.message || '读取流水失败');
      }
    }

    byId('loginBtn').addEventListener('click', login);
    byId('logoutBtn').addEventListener('click', logout);
    byId('reloadConfigBtn').addEventListener('click', loadConfig);
    byId('saveConfigBtn').addEventListener('click', saveConfig);
    byId('clearKeyBtn').addEventListener('click', clearDashscopeApiKey);
    byId('runTestBtn').addEventListener('click', runAsrTest);
    byId('reloadUsersBtn').addEventListener('click', loadUsers);
    byId('reloadChargesBtn').addEventListener('click', loadCharges);
  </script>
</body>
</html>
"""
