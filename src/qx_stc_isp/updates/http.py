"""HTTPS client with fixed host allow-listing. No cookies, tokens or custom CAs."""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlsplit

_ALLOWED_HOSTS = frozenset({
    "github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
})
_TIMEOUT = 15.0  # shortcut: urllib 只有单一 socket 超时；连接/读取未分开，升级时改用 http.client。
_CHUNK = 256 * 1024


class DownloadCancelled(Exception):
    """Raised when the user cancels a download; no partial result is accepted."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    final_url: str

    def header(self, name: str) -> str:
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return ""


def ensure_allowed_url(url: str, *, prefix: str | None = None) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise ValueError("更新只允许 HTTPS 地址")
    if parts.username or parts.password:
        raise ValueError("更新地址不得包含凭据")
    if parts.hostname not in _ALLOWED_HOSTS:
        raise ValueError(f"更新地址主机不在允许列表：{parts.hostname}")
    if prefix is not None and not url.startswith(prefix):
        raise ValueError("更新地址不在固定发布仓库内")


class HttpClient:
    def get(self, url: str, *, headers: Mapping[str, str] | None = None) -> HttpResponse:
        raise NotImplementedError

    def download(
        self,
        url: str,
        destination: Path,
        *,
        on_progress: Callable[[int, int | None], None] | None = None,
        cancel: object | None = None,
        expected_size: int | None = None,
    ) -> None:
        raise NotImplementedError


def _request(url: str, headers: Mapping[str, str] | None) -> urllib.request.Request:
    merged = {"User-Agent": "Astrspire-Updater", "Accept": "application/vnd.github+json"}
    merged.update(headers or {})
    return urllib.request.Request(url, headers=merged)


class UrllibHttpClient(HttpClient):
    def get(self, url: str, *, headers: Mapping[str, str] | None = None) -> HttpResponse:
        ensure_allowed_url(url)
        try:
            response = urllib.request.urlopen(_request(url, headers), timeout=_TIMEOUT)
        except urllib.error.HTTPError as exc:
            with exc:
                body = exc.read(64 * 1024)
            ensure_allowed_url(exc.geturl() or url)
            return HttpResponse(exc.code, dict(exc.headers.items()), body, exc.geturl() or url)
        with response:
            ensure_allowed_url(response.geturl())
            body = response.read(1024 * 1024)
            return HttpResponse(response.status, dict(response.headers.items()), body, response.geturl())

    def download(
        self,
        url: str,
        destination: Path,
        *,
        on_progress: Callable[[int, int | None], None] | None = None,
        cancel: object | None = None,
        expected_size: int | None = None,
    ) -> None:
        ensure_allowed_url(url)
        temporary = Path(str(destination) + ".partial")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urllib.request.urlopen(_request(url, None), timeout=_TIMEOUT) as response:
                ensure_allowed_url(response.geturl())
                total = expected_size or _int_header(response.headers.get("Content-Length"))
                received = 0
                with temporary.open("wb") as stream:
                    while True:
                        if cancel is not None and getattr(cancel, "is_set", lambda: False)():
                            raise DownloadCancelled("更新下载已取消")
                        block = response.read(_CHUNK)
                        if not block:
                            break
                        stream.write(block)
                        received += len(block)
                        if on_progress is not None:
                            on_progress(received, total)
            if expected_size is not None and temporary.stat().st_size != expected_size:
                raise ValueError("下载大小与清单不一致")
            temporary.replace(destination)
        except BaseException:
            if temporary.exists():
                temporary.unlink()
            raise


def _int_header(value: str | None) -> int | None:
    try:
        result = int(value or "")
    except ValueError:
        return None
    return result if result > 0 else None
