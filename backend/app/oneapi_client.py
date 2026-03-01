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


def _normalize_base_and_prefix(
    raw_base_url: str, raw_api_prefix: str
) -> tuple[str, str]:
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
        adjusted_base = urlunsplit(
            (parsed.scheme, parsed.netloc, adjusted_path, parsed.query, parsed.fragment)
        ).rstrip("/")
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
        self._timeout_seconds = max(
            2.0, float(os.getenv("ONEAPI_TIMEOUT_SECONDS", "15"))
        )

    def _build_url(self, path: str) -> str:
        safe_path = str(path or "").strip()
        if not safe_path.startswith("/"):
            safe_path = f"/{safe_path}"
        if safe_path == self._api_prefix or safe_path.startswith(
            f"{self._api_prefix}/"
        ):
            return f"{self._base_url}{safe_path}"
        return f"{self._base_url}{self._api_prefix}{safe_path}"

    @staticmethod
    def _encode_query(params: dict[str, Any]) -> str:
        from urllib.parse import urlencode

        normalized: dict[str, Any] = {}
        for key, value in params.items():
            safe_key = str(key or "").strip()
            if not safe_key:
                continue
            if value is None:
                continue
            text = str(value).strip()
            if text == "":
                continue
            normalized[safe_key] = text
        if not normalized:
            return ""
        return urlencode(normalized)

    @staticmethod
    def _join_path_query(path: str, query: str) -> str:
        safe_path = str(path or "").strip() or "/"
        if not safe_path.startswith("/"):
            safe_path = f"/{safe_path}"
        safe_query = str(query or "").strip()
        if not safe_query:
            return safe_path
        if "?" in safe_path:
            return f"{safe_path}&{safe_query}"
        return f"{safe_path}?{safe_query}"

    @staticmethod
    def _guess_status_code(*, message: str, fallback: int = 400) -> int:
        text = str(message or "").strip().lower()
        if not text:
            return fallback
        if (
            "unauthorized" in text
            or "token" in text
            or "未登录" in text
            or "无权" in text
        ):
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
            raise OneAPIClientError(
                status_code=502,
                code="oneapi_unreachable",
                message=f"OneAPI 网络请求失败: {exc}",
            ) from exc

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
            message = (
                self._extract_error_message(payload)
                or f"OneAPI HTTP {response.status_code}"
            )
            lowered = str(message).lower()
            if "<!doctype html" in lowered or "<html" in lowered:
                message = "OneAPI 返回了 HTML 页面，请检查 ONEAPI_BASE_URL 与 ONEAPI_API_PREFIX"
            if response.status_code == 404:
                raise OneAPIClientError(
                    status_code=404, code="oneapi_route_not_found", message=message
                )
            raise OneAPIClientError(
                status_code=response.status_code,
                code="oneapi_http_error",
                message=message,
            )

        if isinstance(payload, str):
            lowered = payload.lower()
            if (
                lowered.startswith("<!doctype html")
                or lowered.startswith("<html")
                or "<html" in lowered[:160]
            ):
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

    def _request_candidates(
        self,
        method: str,
        candidate_paths: list[str],
        *,
        json_body: dict[str, Any] | None = None,
        access_token: str,
    ) -> Any:
        last_error: OneAPIClientError | None = None
        for path in candidate_paths:
            try:
                return self._request(
                    method,
                    path,
                    json_body=json_body,
                    access_token=access_token,
                )
            except OneAPIClientError as exc:
                last_error = exc
                if exc.status_code in {404, 405}:
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise OneAPIClientError(
            status_code=404,
            code="oneapi_route_not_found",
            message="未找到可用的 OneAPI 管理接口路由",
        )

    @staticmethod
    def _normalize_oneapi_user_item(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        user_id = str(item.get("id") or item.get("user_id") or "").strip()
        username = str(item.get("username") or "").strip()
        if not user_id and username:
            user_id = username
        if not user_id:
            return None
        quota = 0
        used_quota = 0
        request_count = 0
        for key, target in (
            ("quota", "quota"),
            ("used_quota", "used_quota"),
            ("request_count", "request_count"),
        ):
            try:
                if target == "quota":
                    quota = int(item.get(key) or 0)
                elif target == "used_quota":
                    used_quota = int(item.get(key) or 0)
                else:
                    request_count = int(item.get(key) or 0)
            except Exception:
                pass
        role = item.get("role")
        return {
            "id": user_id,
            "user_id": user_id,
            "username": username,
            "quota": max(0, quota),
            "used_quota": max(0, used_quota),
            "request_count": max(0, request_count),
            "remaining_quota": max(0, quota - used_quota),
            "status": item.get("status"),
            "role": role,
            "raw": item,
        }

    def _normalize_user_list_response(self, payload: Any) -> dict[str, Any]:
        if isinstance(payload, list):
            items = [self._normalize_oneapi_user_item(item) for item in payload]
            safe_items = [item for item in items if isinstance(item, dict)]
            return {
                "items": safe_items,
                "total": len(safe_items),
            }
        if not isinstance(payload, dict):
            return {"items": [], "total": 0}

        candidates = []
        for key in ("items", "list", "rows", "users", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates = value
                break
            if isinstance(value, dict):
                for nested_key in ("items", "list", "rows", "users"):
                    nested = value.get(nested_key)
                    if isinstance(nested, list):
                        candidates = nested
                        break
                if candidates:
                    break
        items = [self._normalize_oneapi_user_item(item) for item in candidates]
        safe_items = [item for item in items if isinstance(item, dict)]

        total = len(safe_items)
        for key in ("total", "count", "total_count"):
            try:
                total = max(total, int(payload.get(key) or 0))
            except Exception:
                continue
        return {
            "items": safe_items,
            "total": total,
        }

    def admin_list_users(
        self,
        *,
        access_token: str,
        page: int = 0,
        page_size: int = 100,
        keyword: str = "",
    ) -> dict[str, Any]:
        safe_page = max(0, int(page or 0))
        safe_page_size = max(1, min(200, int(page_size or 100)))
        safe_keyword = str(keyword or "").strip()
        query_candidates = [
            self._encode_query(
                {"p": safe_page, "page_size": safe_page_size, "keyword": safe_keyword}
            ),
            self._encode_query(
                {"page": safe_page, "size": safe_page_size, "keyword": safe_keyword}
            ),
            self._encode_query(
                {
                    "offset": safe_page * safe_page_size,
                    "limit": safe_page_size,
                    "keyword": safe_keyword,
                }
            ),
            self._encode_query({"p": safe_page, "page_size": safe_page_size}),
            self._encode_query({"page": safe_page, "size": safe_page_size}),
        ]
        query_candidates = [query for query in query_candidates if query]
        candidate_paths: list[str] = []
        for base_path in ("/user/", "/user", "/users", "/admin/users"):
            candidate_paths.append(base_path)
            for query in query_candidates:
                candidate_paths.append(self._join_path_query(base_path, query))

        data = self._request_candidates(
            "GET",
            candidate_paths,
            access_token=access_token,
        )
        normalized = self._normalize_user_list_response(data)
        normalized["page"] = safe_page
        normalized["page_size"] = safe_page_size
        normalized["keyword"] = safe_keyword
        return normalized

    def admin_get_user(self, *, access_token: str, user_id: str) -> dict[str, Any]:
        safe_user_id = str(user_id or "").strip()
        if not safe_user_id:
            raise OneAPIClientError(
                status_code=400, code="invalid_user_id", message="user_id 不能为空"
            )
        candidate_paths = [
            f"/user/{safe_user_id}",
            f"/users/{safe_user_id}",
            self._join_path_query("/user", self._encode_query({"id": safe_user_id})),
        ]
        try:
            data = self._request_candidates(
                "GET",
                candidate_paths,
                access_token=access_token,
            )
        except OneAPIClientError:
            data = None
        normalized = self._normalize_oneapi_user_item(data)
        if isinstance(normalized, dict):
            return normalized

        # Fallback: first page scan
        users = self.admin_list_users(
            access_token=access_token,
            page=0,
            page_size=200,
            keyword=safe_user_id,
        )
        for item in users.get("items") or []:
            if not isinstance(item, dict):
                continue
            item_user_id = str(item.get("id") or item.get("user_id") or "").strip()
            item_username = str(item.get("username") or "").strip()
            if safe_user_id in {item_user_id, item_username}:
                return item
        raise OneAPIClientError(
            status_code=404, code="oneapi_user_not_found", message="未找到目标用户"
        )

    def admin_update_user_quota(
        self,
        *,
        access_token: str,
        user_id: str,
        new_quota: int,
    ) -> dict[str, Any]:
        safe_user_id = str(user_id or "").strip()
        if not safe_user_id:
            raise OneAPIClientError(
                status_code=400, code="invalid_user_id", message="user_id 不能为空"
            )
        safe_new_quota = max(0, int(new_quota or 0))

        candidate_payloads = [
            {"id": safe_user_id, "quota": safe_new_quota},
            {"user_id": safe_user_id, "quota": safe_new_quota},
            {"id": safe_user_id, "quota": safe_new_quota, "status": 1},
        ]
        candidate_routes: list[tuple[str, str]] = [
            ("PUT", f"/user/{safe_user_id}"),
            ("POST", f"/user/{safe_user_id}"),
            ("PATCH", f"/user/{safe_user_id}"),
            ("PUT", "/user/"),
            ("PUT", "/user"),
            ("POST", "/user/"),
            ("POST", "/user"),
            ("POST", "/user/topup"),
            ("POST", "/admin/user/topup"),
        ]

        last_error: OneAPIClientError | None = None
        for method, route in candidate_routes:
            for payload in candidate_payloads:
                try:
                    data = self._request(
                        method,
                        route,
                        json_body=payload,
                        access_token=access_token,
                    )
                except OneAPIClientError as exc:
                    last_error = exc
                    if exc.status_code in {404, 405, 400}:
                        continue
                    raise
                normalized = self._normalize_oneapi_user_item(data)
                if isinstance(normalized, dict):
                    return normalized
                # Some routes return only success flag; fetch user to verify
                try:
                    return self.admin_get_user(
                        access_token=access_token, user_id=safe_user_id
                    )
                except OneAPIClientError:
                    continue

        if last_error is not None:
            raise last_error
        raise OneAPIClientError(
            status_code=502,
            code="oneapi_quota_update_failed",
            message="更新用户额度失败",
        )

    def login_admin(
        self,
        *,
        username: str,
        password: str,
    ) -> dict[str, Any]:
        session = self.login(username=username, password=password)
        access_token = self.generate_access_token(session=session)
        user = self.get_self(access_token=access_token)
        return {
            "access_token": access_token,
            "user": user,
        }

    def register(self, *, username: str, password: str) -> dict[str, Any] | None:
        payload = {
            "username": str(username or "").strip(),
            "password": str(password or ""),
        }
        data = self._request("POST", "/user/register", json_body=payload)
        return data if isinstance(data, dict) else None

    def login(self, *, username: str, password: str) -> requests.Session:
        session = requests.Session()
        payload = {
            "username": str(username or "").strip(),
            "password": str(password or ""),
        }
        self._request("POST", "/user/login", json_body=payload, session=session)
        return session

    def generate_access_token(
        self,
        *,
        session: requests.Session | None = None,
        access_token: str | None = None,
    ) -> str:
        data = self._request(
            "GET", "/user/token", session=session, access_token=access_token
        )
        token = str(data or "").strip()
        if not token:
            raise OneAPIClientError(
                status_code=502,
                code="oneapi_invalid_token_response",
                message="OneAPI 未返回 access token",
            )
        return token

    def get_self(self, *, access_token: str) -> dict[str, Any]:
        data = self._request("GET", "/user/self", access_token=access_token)
        if not isinstance(data, dict):
            raise OneAPIClientError(
                status_code=502,
                code="oneapi_invalid_self_response",
                message="OneAPI 用户信息返回异常",
            )
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

    def logout(
        self,
        *,
        access_token: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self._request("GET", "/user/logout", access_token=access_token, session=session)
