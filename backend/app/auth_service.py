from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from app.oneapi_client import OneAPIClient, OneAPIClientError


def _now_ms() -> int:
    from time import time

    return int(round(time() * 1000))


@dataclass
class AuthPrincipal:
    user_id: str
    username: str
    access_token: str
    created_at: int = 0


class AuthError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = int(status_code or 401)
        self.code = str(code or "unauthorized")
        self.message = str(message or "Unauthorized")
        super().__init__(self.message)

    def to_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class AuthService:
    def __init__(self) -> None:
        self._client = OneAPIClient()
        self._token_ttl_hours = max(1, int(float(os.getenv("AUTH_TOKEN_TTL_HOURS", "72"))))

    @staticmethod
    def _normalize_username(username: str) -> str:
        return str(username or "").strip().lower()

    @staticmethod
    def _validate_username(username: str) -> str:
        safe = AuthService._normalize_username(username)
        if len(safe) < 3 or len(safe) > 64:
            raise AuthError(400, "invalid_username", "用户名长度必须在 3 到 64 之间")
        if not safe.replace("_", "").replace("-", "").isalnum():
            raise AuthError(400, "invalid_username", "用户名仅支持字母、数字、下划线和短横线")
        return safe

    @staticmethod
    def _validate_password(password: str) -> str:
        safe = str(password or "")
        if len(safe) < 8 or len(safe) > 128:
            raise AuthError(400, "invalid_password", "密码长度必须在 8 到 128 之间")
        return safe

    @staticmethod
    def _self_to_user_payload(data: dict[str, Any], fallback_username: str = "") -> dict[str, Any]:
        safe = data if isinstance(data, dict) else {}
        user_id = str(safe.get("id") or safe.get("user_id") or "").strip()
        username = str(safe.get("username") or fallback_username or "").strip()
        try:
            created_at = int(safe.get("created_at") or safe.get("createdAt") or 0)
        except Exception:
            created_at = 0
        if created_at <= 0:
            created_at = _now_ms()
        if not user_id:
            user_id = username or "unknown"
        if not username:
            username = "user"
        return {
            "user_id": user_id,
            "username": username,
            "created_at": created_at,
        }

    @staticmethod
    def _extract_quota_payload(data: dict[str, Any], fallback_user: dict[str, Any]) -> dict[str, Any]:
        safe = data if isinstance(data, dict) else {}
        quota = int(safe.get("quota") or 0)
        used_quota = int(safe.get("used_quota") or 0)
        request_count = int(safe.get("request_count") or 0)
        return {
            "user_id": str(safe.get("id") or safe.get("user_id") or fallback_user.get("user_id") or ""),
            "username": str(safe.get("username") or fallback_user.get("username") or ""),
            "quota": max(0, quota),
            "used_quota": max(0, used_quota),
            "request_count": max(0, request_count),
            "remaining_quota": max(0, quota - used_quota),
        }

    @staticmethod
    def _to_auth_error(exc: OneAPIClientError, *, scene: str) -> AuthError:
        safe_message = str(exc.message or "认证失败").strip() or "认证失败"
        lower = safe_message.lower()
        if scene == "register":
            if exc.status_code == 409 or "已存在" in safe_message or "已被使用" in safe_message or "exist" in lower:
                return AuthError(409, "username_exists", "用户名已存在")
            return AuthError(max(400, exc.status_code), "register_failed", safe_message)
        if scene == "login":
            if exc.status_code in {401, 403} or "密码" in safe_message or "登录" in safe_message:
                return AuthError(401, "invalid_credentials", "用户名或密码错误")
            return AuthError(max(400, exc.status_code), "login_failed", safe_message)
        if scene == "authenticate":
            return AuthError(401, "invalid_token", "登录态已失效，请重新登录")
        if scene == "logout":
            return AuthError(max(400, exc.status_code), "logout_failed", safe_message)
        if scene == "redeem":
            return AuthError(max(400, exc.status_code), "redeem_failed", safe_message)
        return AuthError(max(400, exc.status_code), "oneapi_error", safe_message)

    def _build_auth_response(self, *, access_token: str, user: dict[str, Any]) -> dict[str, Any]:
        now = _now_ms()
        expires_at = now + self._token_ttl_hours * 3600 * 1000
        return {
            "token_type": "bearer",
            "access_token": str(access_token or "").strip(),
            "expires_at": expires_at,
            "user": {
                "user_id": str(user.get("user_id") or ""),
                "username": str(user.get("username") or ""),
                "created_at": int(user.get("created_at") or now),
            },
        }

    def _login_and_issue_access_token(self, *, username: str, password: str) -> dict[str, Any]:
        try:
            session = self._client.login(username=username, password=password)
            access_token = self._client.generate_access_token(session=session)
            self_data = self._client.get_self(access_token=access_token)
        except OneAPIClientError as exc:
            raise self._to_auth_error(exc, scene="login") from exc
        user = self._self_to_user_payload(self_data, fallback_username=username)
        return self._build_auth_response(access_token=access_token, user=user)

    def register(self, *, username: str, password: str) -> dict[str, Any]:
        safe_username = self._validate_username(username)
        safe_password = self._validate_password(password)
        try:
            self._client.register(username=safe_username, password=safe_password)
        except OneAPIClientError as exc:
            raise self._to_auth_error(exc, scene="register") from exc
        return self._login_and_issue_access_token(username=safe_username, password=safe_password)

    def login(self, *, username: str, password: str) -> dict[str, Any]:
        safe_username = self._validate_username(username)
        safe_password = self._validate_password(password)
        return self._login_and_issue_access_token(username=safe_username, password=safe_password)

    def authenticate_token(self, token: str) -> AuthPrincipal:
        safe_token = str(token or "").strip()
        if not safe_token:
            raise AuthError(401, "invalid_token", "缺少访问令牌")
        try:
            self_data = self._client.get_self(access_token=safe_token)
        except OneAPIClientError as exc:
            raise self._to_auth_error(exc, scene="authenticate") from exc
        user = self._self_to_user_payload(self_data)
        return AuthPrincipal(
            user_id=str(user.get("user_id") or ""),
            username=str(user.get("username") or ""),
            access_token=safe_token,
            created_at=int(user.get("created_at") or 0),
        )

    def authenticate_request(self, request: Request) -> AuthPrincipal:
        auth_header = str(request.headers.get("authorization") or "").strip()
        if not auth_header:
            raise AuthError(401, "missing_auth_header", "缺少 Authorization 头")
        if not auth_header.lower().startswith("bearer "):
            raise AuthError(401, "invalid_auth_header", "Authorization 头格式应为 Bearer token")
        token = auth_header.split(" ", 1)[1].strip()
        if not token:
            raise AuthError(401, "invalid_auth_header", "Bearer token 不能为空")
        return self.authenticate_token(token)

    def logout(self, principal: AuthPrincipal) -> None:
        safe_token = str(principal.access_token or "").strip()
        if not safe_token:
            raise AuthError(401, "invalid_token", "无效的登录态")
        try:
            self._client.generate_access_token(access_token=safe_token)
        except OneAPIClientError as exc:
            raise self._to_auth_error(exc, scene="logout") from exc

    def get_user_public(self, principal: AuthPrincipal) -> dict[str, Any]:
        try:
            self_data = self._client.get_self(access_token=principal.access_token)
        except OneAPIClientError as exc:
            raise self._to_auth_error(exc, scene="authenticate") from exc
        return self._self_to_user_payload(self_data, fallback_username=principal.username)

    def get_wallet_quota(self, principal: AuthPrincipal) -> dict[str, Any]:
        try:
            self_data = self._client.get_self(access_token=principal.access_token)
        except OneAPIClientError as exc:
            raise self._to_auth_error(exc, scene="authenticate") from exc
        fallback_user = self._self_to_user_payload(self_data, fallback_username=principal.username)
        return self._extract_quota_payload(self_data, fallback_user=fallback_user)

    def redeem_code(self, *, principal: AuthPrincipal, key: str) -> dict[str, Any]:
        safe_key = str(key or "").strip()
        if not safe_key:
            raise AuthError(400, "invalid_redeem_code", "兑换码不能为空")
        try:
            added_quota = self._client.redeem_code(access_token=principal.access_token, key=safe_key)
            self_data = self._client.get_self(access_token=principal.access_token)
        except OneAPIClientError as exc:
            raise self._to_auth_error(exc, scene="redeem") from exc
        fallback_user = self._self_to_user_payload(self_data, fallback_username=principal.username)
        quota_payload = self._extract_quota_payload(self_data, fallback_user=fallback_user)
        return {
            "status": "ok",
            "added_quota": max(0, int(added_quota or 0)),
            **quota_payload,
        }
