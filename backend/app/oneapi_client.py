from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests


def _normalize_base_url(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        value = "http://127.0.0.1:3000"
    return value.rstrip("/")


def _normalize_api_prefix(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        value = "/api"
    if not value.startswith("/"):
        value = f"/{value}"
    return value.rstrip("/")


def _normalize_base_and_prefix(raw_base_url: str, raw_api_prefix: str) -> tuple[str, str]:
    safe_base = _normalize_base_url(raw_base_url)
    safe_prefix = _normalize_api_prefix(raw_api_prefix)
    parsed = urlsplit(safe_base)
    path = str(parsed.path or "").rstrip("/")
    if not path:
        return safe_base, safe_prefix
    if path == safe_prefix or path.endswith(safe_prefix):
        adjusted_path = path[: len(path) - len(safe_prefix)]
        if adjusted_path == "/":
            adjusted_path = ""
        adjusted_base = urlunsplit((parsed.scheme, parsed.netloc, adjusted_path, parsed.query, parsed.fragment)).rstrip("/")
        print(
            "[DEBUG] OneAPI base url contains api prefix. "
            f"Auto-normalized base_url from '{safe_base}' to '{adjusted_base}', prefix='{safe_prefix}'."
        )
        return adjusted_base, safe_prefix
    return safe_base, safe_prefix


@dataclass
class OneAPIClientError(Exception):
    status_code: int
    code: str
    message: str

    def __post_init__(self) -> None:
        super().__init__(self.message)


class OneAPIClient:
    def __init__(self) -> None:
        self._base_url, self._api_prefix = _normalize_base_and_prefix(
            os.getenv("ONEAPI_BASE_URL", "http://127.0.0.1:3000"),
            os.getenv("ONEAPI_API_PREFIX", "/api"),
        )
        self._timeout_seconds = max(2.0, float(os.getenv("ONEAPI_TIMEOUT_SECONDS", "15")))

    def _build_url(self, path: str) -> str:
        safe_path = str(path or "").strip()
        if not safe_path.startswith("/"):
            safe_path = f"/{safe_path}"
        if safe_path == self._api_prefix or safe_path.startswith(f"{self._api_prefix}/"):
            return f"{self._base_url}{safe_path}"
        return f"{self._base_url}{self._api_prefix}{safe_path}"

    @staticmethod
    def _guess_status_code(*, message: str, fallback: int = 400) -> int:
        text = str(message or "").strip().lower()
        if not text:
            return fallback
        if "unauthorized" in text or "token" in text or "未登录" in text or "无权" in text:
            return 401
        if "forbidden" in text or "权限" in text:
            return 403
        if "exist" in text or "已存在" in text or "已被使用" in text:
            return 409
        if "invalid" in text or "参数" in text:
            return 400
        return fallback

    @staticmethod
    def _extract_error_message(payload: Any) -> str:
        if isinstance(payload, dict):
            return str(payload.get("message") or payload.get("error") or "").strip()
        if isinstance(payload, str):
            return payload.strip()
        return ""

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        access_token: str | None = None,
        session: requests.Session | None = None,
    ) -> Any:
        client = session if session else requests
        headers = {"Accept": "application/json"}
        safe_token = str(access_token or "").strip()
        if safe_token:
            headers["Authorization"] = f"Bearer {safe_token}"
        try:
            response = client.request(
                method=method,
                url=self._build_url(path),
                json=json_body,
                headers=headers,
                timeout=self._timeout_seconds,
            )
        except requests.RequestException as exc:
            raise OneAPIClientError(status_code=502, code="oneapi_unreachable", message=f"OneAPI 网络请求失败: {exc}") from exc

        content_type = str(response.headers.get("content-type") or "").lower()
        if "application/json" in content_type:
            try:
                payload: Any = response.json()
            except Exception as exc:
                raise OneAPIClientError(
                    status_code=502,
                    code="oneapi_invalid_json_response",
                    message=f"OneAPI 返回了无法解析的 JSON: {exc}",
                ) from exc
        else:
            payload = str(response.text or "").strip()

        if response.status_code >= 400:
            message = self._extract_error_message(payload) or f"OneAPI HTTP {response.status_code}"
            lowered = str(message).lower()
            if "<!doctype html" in lowered or "<html" in lowered:
                message = "OneAPI 返回了 HTML 页面，请检查 ONEAPI_BASE_URL 与 ONEAPI_API_PREFIX"
            if response.status_code == 404:
                raise OneAPIClientError(status_code=404, code="oneapi_route_not_found", message=message)
            raise OneAPIClientError(status_code=response.status_code, code="oneapi_http_error", message=message)

        if isinstance(payload, str):
            lowered = payload.lower()
            if lowered.startswith("<!doctype html") or lowered.startswith("<html") or "<html" in lowered[:160]:
                print(
                    "[DEBUG] OneAPI returned HTML payload with success status. "
                    f"url={self._build_url(path)} content_type={content_type or '-'}"
                )
                raise OneAPIClientError(
                    status_code=502,
                    code="oneapi_unexpected_html",
                    message="OneAPI 返回了 HTML 页面，请检查 ONEAPI_BASE_URL 与 ONEAPI_API_PREFIX",
                )
            return payload

        if isinstance(payload, dict):
            success = payload.get("success")
            if success is False:
                message = self._extract_error_message(payload) or "OneAPI 请求失败"
                raise OneAPIClientError(
                    status_code=self._guess_status_code(message=message, fallback=400),
                    code="oneapi_request_failed",
                    message=message,
                )
            if "data" in payload:
                return payload.get("data")
            return payload
        return payload

    def register(self, *, username: str, password: str) -> dict[str, Any] | None:
        payload = {"username": str(username or "").strip(), "password": str(password or "")}
        data = self._request("POST", "/user/register", json_body=payload)
        return data if isinstance(data, dict) else None

    def login(self, *, username: str, password: str) -> requests.Session:
        session = requests.Session()
        payload = {"username": str(username or "").strip(), "password": str(password or "")}
        self._request("POST", "/user/login", json_body=payload, session=session)
        return session

    def generate_access_token(
        self,
        *,
        session: requests.Session | None = None,
        access_token: str | None = None,
    ) -> str:
        data = self._request("GET", "/user/token", session=session, access_token=access_token)
        token = str(data or "").strip()
        if not token:
            raise OneAPIClientError(status_code=502, code="oneapi_invalid_token_response", message="OneAPI 未返回 access token")
        return token

    def get_self(self, *, access_token: str) -> dict[str, Any]:
        data = self._request("GET", "/user/self", access_token=access_token)
        if not isinstance(data, dict):
            raise OneAPIClientError(status_code=502, code="oneapi_invalid_self_response", message="OneAPI 用户信息返回异常")
        return data

    def redeem_code(self, *, access_token: str, key: str) -> int:
        data = self._request(
            "POST",
            "/user/topup",
            json_body={"key": str(key or "").strip()},
            access_token=access_token,
        )
        if isinstance(data, (int, float)):
            return int(data)
        try:
            return int(str(data or "0").strip())
        except Exception:
            return 0

    def logout(self, *, access_token: str | None = None, session: requests.Session | None = None) -> None:
        self._request("GET", "/user/logout", access_token=access_token, session=session)
