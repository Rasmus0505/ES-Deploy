from __future__ import annotations

import importlib.util
import functools
import hashlib
import ipaddress
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse, urlunparse

from vendor.videolingo_subtitle_core.engine import PipelineError


CancelCheck = Callable[[], bool]
ProgressCallback = Callable[[int, str], None]

_LOCAL_YTDLP_ENTRY_DEFAULT = ""
_DOWNLOAD_TIMEOUT_SECONDS = 900
_URL_SCAN_PATTERN = re.compile(r"https?://[^\s<>'\"`]+", re.IGNORECASE)
_URL_TRAILING_TRIM = re.compile(r"[)\]}>,.;!?。！？；，、》】）]+$")
_URL_INLINE_BREAK = re.compile(r"[，。！？；、）】》]")
_AUTO_DISCOVER_SEARCH_ROOTS_DEFAULT: tuple[Path, ...] = ()
_AUTO_DISCOVER_LIMIT = 20
_CACHE_LOCK = threading.RLock()
_CACHE_TTL_SECONDS = max(1, int(float(os.getenv("URL_SOURCE_CACHE_TTL_DAYS", "14")))) * 24 * 3600
_CACHE_MAX_BYTES = max(1024 * 1024, int(float(os.getenv("URL_SOURCE_CACHE_MAX_GB", "30")) * 1024 * 1024 * 1024))
_CACHE_ROOT = Path(__file__).resolve().parents[1] / "runtime" / "source-cache"
_CACHE_DB = _CACHE_ROOT / "index.sqlite3"
_DOWNLOAD_CONCURRENCY_LIMIT = max(1, int(float(os.getenv("URL_SOURCE_DOWNLOAD_CONCURRENCY", "3"))))
_DOWNLOAD_SEMAPHORE = threading.BoundedSemaphore(_DOWNLOAD_CONCURRENCY_LIMIT)
_DEFAULT_YTDLP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_DEFAULT_BILIBILI_REFERER = "https://www.bilibili.com/"
_DEFAULT_BILIBILI_ORIGIN = "https://www.bilibili.com"
_SOURCE_ALLOWED_DOMAINS_DEFAULT = (
    "youtube.com",
    "youtu.be",
    "bilibili.com",
    "b23.tv",
)
_SOURCE_ALLOWED_DOMAINS = tuple(
    item.strip().lower()
    for item in str(os.getenv("URL_SOURCE_ALLOWED_DOMAINS", ",".join(_SOURCE_ALLOWED_DOMAINS_DEFAULT))).split(",")
    if str(item or "").strip()
)


def _is_valid_http_url(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    parsed = urlparse(text)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _extract_http_url_candidates(raw: str) -> list[str]:
    source = str(raw or "")
    if not source:
        return []

    candidates: list[str] = []
    seen: set[str] = set()
    for matched in _URL_SCAN_PATTERN.findall(source):
        cleaned = _URL_TRAILING_TRIM.sub("", str(matched or "").strip())
        split_match = _URL_INLINE_BREAK.search(cleaned)
        if split_match:
            cleaned = cleaned[:split_match.start()].strip()
        if not _is_valid_http_url(cleaned):
            continue
        dedup_key = cleaned.lower()
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        candidates.append(cleaned)
    return candidates


def _normalize_source_url_format(url: str) -> str:
    value = str(url or "").strip()
    if _is_valid_http_url(value):
        parsed = urlparse(value)
        normalized_path = parsed.path or "/"
        normalized_query = parsed.query or ""
        return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), normalized_path, "", normalized_query, ""))

    candidates = _extract_http_url_candidates(value)
    if candidates:
        candidate = candidates[0]
        parsed = urlparse(candidate)
        normalized_path = parsed.path or "/"
        normalized_query = parsed.query or ""
        return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), normalized_path, "", normalized_query, ""))

    raise PipelineError(
        stage="download_source",
        code="invalid_source_url",
        message="视频链接无效，请输入完整的 http(s) 链接",
        detail=f"url={value[:200]}",
    )


def _parse_host_from_url(url: str) -> str:
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
    except Exception:
        return ""
    return str(parsed.hostname or "").strip().lower()


def _is_loopback_hostname(host: str) -> bool:
    safe = str(host or "").strip().lower()
    if not safe:
        return False
    return safe in {"localhost", "ip6-localhost"} or safe.endswith(".localhost")


def _is_blocked_ip(ip_text: str) -> bool:
    try:
        ip_obj = ipaddress.ip_address(str(ip_text or "").strip())
    except Exception:
        return False
    return bool(
        ip_obj.is_loopback
        or ip_obj.is_private
        or ip_obj.is_link_local
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
        or ip_obj.is_multicast
    )


def _resolve_host_ips(host: str) -> set[str]:
    safe_host = str(host or "").strip()
    if not safe_host:
        return set()
    try:
        addresses = socket.getaddrinfo(safe_host, None, type=socket.SOCK_STREAM)
    except Exception:
        return set()
    resolved: set[str] = set()
    for item in addresses:
        sockaddr = item[4]
        if not isinstance(sockaddr, tuple) or not sockaddr:
            continue
        ip_text = str(sockaddr[0] or "").strip()
        if ip_text:
            resolved.add(ip_text)
    return resolved


def _host_matches_allowlist(host: str) -> bool:
    safe_host = str(host or "").strip().lower()
    if not safe_host:
        return False
    if not _SOURCE_ALLOWED_DOMAINS:
        return True
    for domain in _SOURCE_ALLOWED_DOMAINS:
        safe_domain = str(domain or "").strip().lower()
        if not safe_domain:
            continue
        if safe_host == safe_domain or safe_host.endswith(f".{safe_domain}"):
            return True
    return False


def evaluate_source_url_policy(url: str) -> dict[str, str | bool]:
    normalized_url = _normalize_source_url_format(url)
    host = _parse_host_from_url(normalized_url)
    if not host:
        return {
            "normalized_url": normalized_url,
            "host": "",
            "allowed": False,
            "reason": "url_host_missing",
        }
    if _is_loopback_hostname(host):
        return {
            "normalized_url": normalized_url,
            "host": host,
            "allowed": False,
            "reason": "host_loopback_not_allowed",
        }

    host_ip_literal = ""
    try:
        host_ip_literal = str(ipaddress.ip_address(host))
    except Exception:
        host_ip_literal = ""

    if host_ip_literal and _is_blocked_ip(host_ip_literal):
        return {
            "normalized_url": normalized_url,
            "host": host,
            "allowed": False,
            "reason": "host_private_ip_not_allowed",
        }

    for resolved_ip in _resolve_host_ips(host):
        if _is_blocked_ip(resolved_ip):
            return {
                "normalized_url": normalized_url,
                "host": host,
                "allowed": False,
                "reason": "dns_private_ip_not_allowed",
            }

    if not _host_matches_allowlist(host):
        return {
            "normalized_url": normalized_url,
            "host": host,
            "allowed": False,
            "reason": "host_not_in_allowlist",
        }

    return {
        "normalized_url": normalized_url,
        "host": host,
        "allowed": True,
        "reason": "ok",
    }


def _iter_search_roots() -> list[Path]:
    env_roots = str(os.getenv("YT_DLP_SEARCH_ROOTS", "")).strip()
    candidates: list[Path] = []
    if env_roots:
        for item in env_roots.split(os.pathsep):
            value = str(item or "").strip()
            if not value:
                continue
            candidates.append(Path(value).expanduser())
    else:
        candidates.extend(_AUTO_DISCOVER_SEARCH_ROOTS_DEFAULT)

    deduped: list[Path] = []
    seen: set[str] = set()
    for root in candidates:
        normalized = str(root.resolve()) if root.exists() else str(root)
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(root)
    return deduped


@functools.lru_cache(maxsize=1)
def _discover_local_yt_dlp_entries() -> tuple[str, ...]:
    found: list[str] = []
    seen: set[str] = set()

    def add_if_entry(path: Path) -> None:
        if not path.is_file():
            return
        key = str(path.resolve()).lower()
        if key in seen:
            return
        seen.add(key)
        found.append(str(path.resolve()))

    for root in _iter_search_roots():
        if not root.exists() or not root.is_dir():
            continue
        # 常见目录先尝试，减少递归成本。
        direct_patterns = (
            "yt-dlp/yt_dlp/__main__.py",
            "yt-dlp*/yt_dlp/__main__.py",
            "*yt-dlp*/yt_dlp/__main__.py",
            "前端项目/yt-dlp/yt_dlp/__main__.py",
            "前端项目/yt-dlp*/yt_dlp/__main__.py",
        )
        for pattern in direct_patterns:
            for path in root.glob(pattern):
                add_if_entry(path)
                if len(found) >= _AUTO_DISCOVER_LIMIT:
                    return tuple(found)

        # 回退到有限递归扫描，兼容用户自定义目录名。
        for path in root.rglob("yt_dlp/__main__.py"):
            add_if_entry(path)
            if len(found) >= _AUTO_DISCOVER_LIMIT:
                return tuple(found)

    return tuple(found)


def normalize_source_url(url: str) -> str:
    policy = evaluate_source_url_policy(url)
    if bool(policy.get("allowed")):
        return str(policy.get("normalized_url") or "")
    reason = str(policy.get("reason") or "source_url_not_allowed")
    raise PipelineError(
        stage="download_source",
        code="source_url_not_allowed",
        message="链接不在允许范围（仅支持公开视频站点，且拒绝内网地址）",
        detail=f"reason={reason}; host={policy.get('host')}; url={policy.get('normalized_url')}",
    )


def _safe_now_ts() -> int:
    return int(time.time())


def _compute_file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _cache_key_from_url(url: str) -> str:
    return hashlib.sha256(str(url or "").strip().encode("utf-8")).hexdigest()


def _ensure_cache_db() -> None:
    with _CACHE_LOCK:
        _CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(_CACHE_DB), timeout=10) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS url_source_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    normalized_url TEXT NOT NULL,
                    url_key TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL DEFAULT 0,
                    last_accessed_at INTEGER NOT NULL DEFAULT 0,
                    hit_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_url_source_cache_url ON url_source_cache(normalized_url, last_accessed_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_url_source_cache_access ON url_source_cache(last_accessed_at ASC)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_url_source_cache_uniq ON url_source_cache(normalized_url, content_sha256)"
            )
            connection.commit()


def _delete_cache_row(connection: sqlite3.Connection, row_id: int, local_path: str) -> None:
    try:
        path = Path(local_path)
        if path.is_file():
            path.unlink(missing_ok=True)
    except Exception:
        pass
    connection.execute("DELETE FROM url_source_cache WHERE id = ?", (int(row_id),))


def _prune_cache_locked(connection: sqlite3.Connection) -> None:
    now = _safe_now_ts()
    expire_before = now - _CACHE_TTL_SECONDS
    rows = connection.execute(
        "SELECT id, local_path, size_bytes, last_accessed_at FROM url_source_cache ORDER BY last_accessed_at ASC, id ASC"
    ).fetchall()
    total_size = 0
    alive_rows: list[tuple[int, str, int, int]] = []
    for row in rows:
        row_id = int(row[0])
        local_path = str(row[1] or "")
        size_bytes = int(row[2] or 0)
        last_accessed_at = int(row[3] or 0)
        path = Path(local_path)
        if not path.is_file():
            _delete_cache_row(connection, row_id, local_path)
            continue
        if last_accessed_at <= 0 or last_accessed_at < expire_before:
            _delete_cache_row(connection, row_id, local_path)
            continue
        safe_size = size_bytes if size_bytes > 0 else int(path.stat().st_size)
        alive_rows.append((row_id, local_path, safe_size, last_accessed_at))
        total_size += safe_size

    if total_size <= _CACHE_MAX_BYTES:
        return
    for row_id, local_path, safe_size, _last_accessed_at in alive_rows:
        if total_size <= _CACHE_MAX_BYTES:
            break
        _delete_cache_row(connection, row_id, local_path)
        total_size = max(0, total_size - max(0, safe_size))


def _cache_lookup(normalized_url: str) -> Path | None:
    _ensure_cache_db()
    with _CACHE_LOCK:
        with sqlite3.connect(str(_CACHE_DB), timeout=10) as connection:
            _prune_cache_locked(connection)
            row = connection.execute(
                """
                SELECT id, local_path, hit_count
                FROM url_source_cache
                WHERE normalized_url = ?
                ORDER BY last_accessed_at DESC, id DESC
                LIMIT 1
                """,
                (normalized_url,),
            ).fetchone()
            if not row:
                connection.commit()
                return None
            row_id = int(row[0])
            local_path = str(row[1] or "")
            cache_path = Path(local_path)
            if not cache_path.is_file():
                _delete_cache_row(connection, row_id, local_path)
                connection.commit()
                return None
            now = _safe_now_ts()
            next_hit = int(row[2] or 0) + 1
            connection.execute(
                "UPDATE url_source_cache SET last_accessed_at = ?, hit_count = ? WHERE id = ?",
                (now, next_hit, row_id),
            )
            connection.commit()
            return cache_path


def _record_downloaded_file_to_cache(*, normalized_url: str, downloaded_path: Path) -> None:
    if not downloaded_path.is_file():
        return
    _ensure_cache_db()
    now = _safe_now_ts()
    content_sha = _compute_file_sha256(downloaded_path)
    suffix = downloaded_path.suffix.lower() or ".mp4"
    cached_path = _CACHE_ROOT / f"{content_sha}{suffix}"
    if not cached_path.is_file():
        cached_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(downloaded_path, cached_path)
        except Exception:
            return
    size_bytes = int(cached_path.stat().st_size) if cached_path.is_file() else int(downloaded_path.stat().st_size)
    with _CACHE_LOCK:
        with sqlite3.connect(str(_CACHE_DB), timeout=10) as connection:
            connection.execute(
                """
                INSERT INTO url_source_cache(
                    normalized_url, url_key, content_sha256, local_path, size_bytes,
                    created_at, last_accessed_at, hit_count
                ) VALUES(?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(normalized_url, content_sha256) DO UPDATE SET
                    local_path=excluded.local_path,
                    size_bytes=excluded.size_bytes,
                    last_accessed_at=excluded.last_accessed_at
                """,
                (
                    normalized_url,
                    _cache_key_from_url(normalized_url),
                    content_sha,
                    str(cached_path),
                    size_bytes,
                    now,
                    now,
                ),
            )
            _prune_cache_locked(connection)
            connection.commit()


def _materialize_cached_video(*, cached_video: Path, output_root: Path) -> Path:
    marker = f"source_cache_{int(time.time() * 1000)}"
    target = output_root / f"{marker}{cached_video.suffix.lower() or '.mp4'}"
    output_root.mkdir(parents=True, exist_ok=True)
    try:
        os.link(str(cached_video), str(target))
    except Exception:
        shutil.copy2(cached_video, target)
    return target


def download_video_from_url(
    source_url: str,
    output_dir: str | Path,
    *,
    should_cancel: CancelCheck | None = None,
    on_progress: ProgressCallback | None = None,
    timeout_seconds: int = _DOWNLOAD_TIMEOUT_SECONDS,
) -> str:
    safe_url = normalize_source_url(source_url)
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    cached_hit = _cache_lookup(safe_url)
    if cached_hit and cached_hit.is_file():
        if callable(on_progress):
            on_progress(95, "命中视频缓存，正在复用已下载素材")
        materialized = _materialize_cached_video(cached_video=cached_hit, output_root=output_root)
        if callable(on_progress):
            on_progress(100, "已复用缓存视频，准备提取音频")
        print(f"[DEBUG] URL ingest cache hit: url={safe_url} file={cached_hit}")
        return str(materialized)

    commands = _resolve_yt_dlp_commands()
    if not commands:
        raise PipelineError(
            stage="download_source",
            code="yt_dlp_not_available",
            message="未找到 yt-dlp，可用入口缺失",
            detail="请检查 YT_DLP_LOCAL_ENTRY、YT_DLP_EXECUTABLE、YT_DLP_SEARCH_ROOTS 或 PATH 中的 yt-dlp 可执行文件",
        )

    acquired = _DOWNLOAD_SEMAPHORE.acquire(timeout=8)
    if not acquired:
        raise PipelineError(
            stage="download_source",
            code="download_concurrency_limit",
            message="下载任务繁忙，请稍后重试",
            detail=f"limit={_DOWNLOAD_CONCURRENCY_LIMIT}",
        )
    try:
        last_error: PipelineError | None = None
        for command, source in commands:
            try:
                print(f"[DEBUG] URL ingest using yt-dlp source: {source}")
                downloaded = _run_download(
                    command=command,
                    source_url=safe_url,
                    output_root=output_root,
                    should_cancel=should_cancel,
                    on_progress=on_progress,
                    timeout_seconds=timeout_seconds,
                )
                try:
                    _record_downloaded_file_to_cache(normalized_url=safe_url, downloaded_path=Path(downloaded))
                except Exception as cache_exc:
                    print(f"[DEBUG] URL ingest cache store failed: {cache_exc}")
                print(f"[DEBUG] URL ingest downloaded file: {downloaded}")
                return downloaded
            except PipelineError as exc:
                last_error = exc
                if exc.code in {"yt_dlp_launch_failed", "yt_dlp_command_failed", "download_output_missing"}:
                    print(f"[DEBUG] yt-dlp source failed ({source}): {exc.code} -> {exc.message}")
                    continue
                raise

        detail = last_error.detail if last_error else "unknown"
        raise PipelineError(
            stage="download_source",
            code="download_failed",
            message="链接素材下载失败",
            detail=detail,
        )
    finally:
        _DOWNLOAD_SEMAPHORE.release()


def _resolve_yt_dlp_commands() -> list[tuple[list[str], str]]:
    commands: list[tuple[list[str], str]] = []

    local_entry_value = str(os.getenv("YT_DLP_LOCAL_ENTRY", _LOCAL_YTDLP_ENTRY_DEFAULT)).strip()
    if local_entry_value:
        local_entry = Path(local_entry_value).expanduser()
        if local_entry.is_file():
            commands.append(([sys.executable, str(local_entry)], f"local-entry:{local_entry}"))

    for discovered in _discover_local_yt_dlp_entries():
        commands.append(([sys.executable, discovered], f"auto-discovered:{discovered}"))

    configured_exec = str(os.getenv("YT_DLP_EXECUTABLE", "")).strip()
    if configured_exec:
        commands.append(([configured_exec], f"env-exec:{configured_exec}"))

    which_exec = shutil.which("yt-dlp")
    if which_exec:
        commands.append(([which_exec], f"path-exec:{which_exec}"))

    if importlib.util.find_spec("yt_dlp") is not None:
        commands.append(([sys.executable, "-m", "yt_dlp"], "python-module:yt_dlp"))

    deduped: list[tuple[list[str], str]] = []
    seen: set[str] = set()
    for command, source in commands:
        key = "\u0000".join(command)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((command, source))
    return deduped


def _is_bilibili_source_url(url: str) -> bool:
    host = _parse_host_from_url(url)
    if not host:
        return False
    return host == "bilibili.com" or host.endswith(".bilibili.com") or host == "b23.tv" or host.endswith(".b23.tv")


def _resolve_yt_dlp_cookies_args() -> list[str]:
    cookies_file_raw = str(os.getenv("YT_DLP_COOKIES_FILE", "")).strip()
    if not cookies_file_raw:
        return []
    cookies_file = Path(cookies_file_raw).expanduser()
    if not cookies_file.is_file():
        print(f"[DEBUG] YT_DLP_COOKIES_FILE not found: {cookies_file}")
        return []
    return ["--cookies", str(cookies_file)]


def _resolve_bilibili_cookie_header() -> str:
    raw_cookie = str(os.getenv("YT_DLP_BILIBILI_COOKIE", "")).strip()
    if not raw_cookie:
        return ""
    sanitized = re.sub(r"[\r\n]+", " ", raw_cookie).strip()
    if sanitized.lower().startswith("cookie:"):
        sanitized = sanitized.split(":", 1)[1].strip()
    return sanitized


def _build_yt_dlp_request_args(source_url: str) -> list[str]:
    args: list[str] = []
    user_agent = str(os.getenv("YT_DLP_USER_AGENT", "")).strip() or _DEFAULT_YTDLP_USER_AGENT
    if user_agent:
        args.extend(["--user-agent", user_agent])

    if _is_bilibili_source_url(source_url):
        referer = str(os.getenv("YT_DLP_BILIBILI_REFERER", _DEFAULT_BILIBILI_REFERER)).strip() or _DEFAULT_BILIBILI_REFERER
        origin = str(os.getenv("YT_DLP_BILIBILI_ORIGIN", _DEFAULT_BILIBILI_ORIGIN)).strip() or _DEFAULT_BILIBILI_ORIGIN
        bilibili_cookie_header = _resolve_bilibili_cookie_header()
        args.extend(["--add-header", f"Referer:{referer}"])
        args.extend(["--add-header", f"Origin:{origin}"])
        args.extend(["--add-header", "Accept-Language:zh-CN,zh;q=0.9,en;q=0.8"])
        if bilibili_cookie_header:
            print("[DEBUG] Bilibili cookie header enabled via YT_DLP_BILIBILI_COOKIE")
            args.extend(["--add-header", f"Cookie:{bilibili_cookie_header}"])
        else:
            args.extend(_resolve_yt_dlp_cookies_args())

    return args


def _run_download(
    *,
    command: list[str],
    source_url: str,
    output_root: Path,
    should_cancel: CancelCheck | None,
    on_progress: ProgressCallback | None,
    timeout_seconds: int,
) -> str:
    safe_timeout = max(60, int(timeout_seconds or _DOWNLOAD_TIMEOUT_SECONDS))
    marker = f"source_{int(time.time() * 1000)}"
    output_template = str((output_root / f"{marker}.%(ext)s").resolve())
    request_args = _build_yt_dlp_request_args(source_url)

    args = [
        *command,
        "--no-playlist",
        "--no-progress",
        "--newline",
        "--restrict-filenames",
        "--format",
        "bv*+ba/b",
        "--merge-output-format",
        "mp4",
        "--output",
        output_template,
        *request_args,
        "--",
        source_url,
    ]

    if request_args:
        print(f"[DEBUG] yt-dlp extra request args enabled for url={source_url}")

    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:
        raise PipelineError(
            stage="download_source",
            code="yt_dlp_launch_failed",
            message="无法启动 yt-dlp 下载进程",
            detail=str(exc)[:500],
        ) from exc

    started_at = time.monotonic()
    last_progress_second = -1
    while True:
        if callable(should_cancel) and bool(should_cancel()):
            _terminate_process(process)
            raise PipelineError(
                stage="download_source",
                code="cancel_requested",
                message="任务取消请求已接收，已停止下载",
            )

        return_code = process.poll()
        if return_code is not None:
            break

        elapsed = time.monotonic() - started_at
        elapsed_sec = max(0, int(elapsed))
        if callable(on_progress) and elapsed_sec != last_progress_second:
            # yt-dlp 真实下载百分比不可稳定提取时，按耗时给出细颗粒心跳进度。
            pseudo_percent = max(0, min(95, 5 + elapsed_sec * 3))
            on_progress(pseudo_percent, "正在解析并下载素材链接")
            last_progress_second = elapsed_sec
        if elapsed > safe_timeout:
            _terminate_process(process)
            raise PipelineError(
                stage="download_source",
                code="download_timeout",
                message="下载超时，请稍后重试",
                detail=f"timeout_seconds={safe_timeout}",
            )
        time.sleep(0.3)

    stdout, stderr = process.communicate()
    if process.returncode != 0:
        detail_text = _build_failure_detail(stdout=stdout, stderr=stderr)
        raise PipelineError(
            stage="download_source",
            code="yt_dlp_command_failed",
            message="yt-dlp 下载命令执行失败",
            detail=detail_text,
        )

    resolved = _resolve_downloaded_media_file(output_root=output_root, marker=marker)
    if not resolved:
        detail_text = _build_failure_detail(stdout=stdout, stderr=stderr)
        raise PipelineError(
            stage="download_source",
            code="download_output_missing",
            message="下载完成但未找到可用视频文件",
            detail=detail_text,
        )

    if callable(on_progress):
        on_progress(100, "素材下载完成，准备提取音频")

    return str(resolved)


def _resolve_downloaded_media_file(*, output_root: Path, marker: str) -> Path | None:
    candidates = []
    for path in output_root.glob(f"{marker}.*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in {
            ".part",
            ".ytdl",
            ".json",
            ".description",
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".vtt",
            ".srt",
            ".ass",
            ".lrc",
            ".txt",
        }:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size <= 0:
            continue
        candidates.append(path)

    if not candidates:
        return None

    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[0]


def _build_failure_detail(*, stdout: str, stderr: str) -> str:
    text = "\n".join([
        str(stderr or "").strip(),
        str(stdout or "").strip(),
    ]).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return "yt-dlp command failed without diagnostic output"

    lowered = text.lower()
    if ("bilibili" in lowered or "b23.tv" in lowered) and ("http error 412" in lowered or "precondition failed" in lowered):
        text = (
            "B站风控拦截（HTTP 412）。请在后端配置 YT_DLP_COOKIES_FILE（cookies.txt）或 YT_DLP_BILIBILI_COOKIE（Cookie 字符串）后重试。"
            f" 原始错误: {text}"
        )

    return text[:900]


def _terminate_process(process: subprocess.Popen[str]) -> None:
    try:
        process.terminate()
    except Exception:
        return
    try:
        process.wait(timeout=3)
    except Exception:
        try:
            process.kill()
        except Exception:
            return
