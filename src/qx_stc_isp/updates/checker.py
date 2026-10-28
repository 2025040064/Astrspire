"""Anonymous release check and verified download. No tokens, no UI."""

from __future__ import annotations

import json
import socket
import time
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from .config import (
    ASSISTANT_PROTOCOL, LATEST_RELEASE_URL, MANIFEST_ASSET, RELEASE_DOWNLOAD_PREFIX,
    SIGNATURE_ASSET, UpdatePaths, UpdatePreferences, asset_name, atomic_write,
)
from .http import DownloadCancelled, HttpClient, HttpResponse, UrllibHttpClient, ensure_allowed_url
from .manifest import (
    UpdateError, UpdateManifest, VerifiedUpdate, compare_versions, decode_signature,
    file_sha256, parse_version, verify_signature, verify_update,
)

_MAX_METADATA_BYTES = 128 * 1024
_MIN_FREE_BYTES = 64 * 1024 * 1024
_DOWNLOAD_ATTEMPTS = 2


@dataclass(frozen=True)
class CheckResult:
    status: str  # available / up_to_date / skipped / unconfigured / manual / error
    message: str
    manifest: UpdateManifest | None = None
    asset_url: str = ""


class UpdateChecker:
    def __init__(
        self,
        paths: UpdatePaths,
        current_version: str,
        *,
        preferences: UpdatePreferences | None = None,
        public_key: bytes = b"",
        client: HttpClient | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.paths = paths
        self.current_version = current_version
        self.preferences = preferences or UpdatePreferences()
        self.public_key = public_key
        self.client = client or UrllibHttpClient()
        self._sleep = sleep

    # ---- release check -------------------------------------------------
    def check(self, *, manual: bool = False) -> CheckResult:
        if not self.public_key:
            return CheckResult("unconfigured", "未配置更新公钥，无法验证更新来源。")
        try:
            parse_version(self.current_version)
        except ValueError:
            return CheckResult("error", "当前为开发版本，未参与自动更新。")
        now = datetime.now(timezone.utc)
        if not manual and self._retry_blocked(now):
            return CheckResult("error", "GitHub 请求受限，将在稍后自动重试。")
        headers = {"Accept": "application/vnd.github+json"}
        if self.preferences.etag and not manual:
            headers["If-None-Match"] = self.preferences.etag
        try:
            response = self._get_with_retry(LATEST_RELEASE_URL, headers)
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, ValueError) as exc:
            return CheckResult("error", f"检查更新失败：{exc}")
        self.preferences.last_check_utc = now.isoformat(timespec="seconds")
        if response.status == 304:
            return CheckResult("up_to_date", "已是最新版本。")
        if response.status in (403, 429):
            self.preferences.retry_after_utc = _retry_deadline(response) or ""
            return CheckResult("error", "GitHub 访问受限或限流，稍后再试。")
        if response.status != 200:
            return CheckResult("error", f"检查更新失败：HTTP {response.status}")
        etag = response.header("ETag")
        if etag:
            self.preferences.etag = etag
        try:
            release = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return CheckResult("error", "发布信息不是有效 JSON")
        if not isinstance(release, dict):
            return CheckResult("error", "发布信息结构无效")
        if release.get("draft") or release.get("prerelease"):
            return CheckResult("up_to_date", "最新稳定版无需更新。")
        tag = release.get("tag_name")
        if not isinstance(tag, str):
            return CheckResult("error", "发布信息缺少版本 Tag")
        version = tag[1:] if tag.startswith("v") else tag
        try:
            parse_version(version)
        except ValueError as exc:
            return CheckResult("error", str(exc))
        if compare_versions(version, self.current_version) <= 0:
            return CheckResult("up_to_date", "已是最新版本。")
        assets = _asset_map(release)
        try:
            manifest_url = _asset_url(assets, MANIFEST_ASSET)
            signature_url = _asset_url(assets, SIGNATURE_ASSET)
            package_url = _asset_url(assets, asset_name(version))
            manifest_raw = self._get_metadata(manifest_url)
            signature_raw = self._get_metadata(signature_url)
            signature = decode_signature(signature_raw)
            verify_signature(manifest_raw, signature, self.public_key)
            manifest = UpdateManifest.parse(manifest_raw)
        except UpdateError as exc:
            return CheckResult("error", str(exc))
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, ValueError) as exc:
            return CheckResult("error", f"获取更新清单失败：{exc}")
        if manifest.version != version:
            return CheckResult("error", "更新清单版本与发布 Tag 不一致")
        try:
            directory = self.paths.download_dir(manifest.version)
            atomic_write(directory / MANIFEST_ASSET, manifest_raw)
            atomic_write(directory / SIGNATURE_ASSET, signature_raw)
        except OSError as exc:
            return CheckResult("error", f"无法保存更新清单：{exc}")
        if compare_versions(manifest.version, self.current_version) <= 0:
            return CheckResult("up_to_date", "已是最新版本。")
        if manifest.min_assistant > ASSISTANT_PROTOCOL:
            return CheckResult("manual", "该版本需要更高版本的更新助手，请手动下载安装。", manifest)
        if not manual and self.preferences.skipped_version == manifest.version:
            return CheckResult("skipped", f"已跳过版本 {manifest.version}。", manifest, package_url)
        return CheckResult("available", f"发现新版本 {manifest.version}。", manifest, package_url)

    def _retry_blocked(self, now: datetime) -> bool:
        if not self.preferences.retry_after_utc:
            return False
        try:
            return datetime.fromisoformat(self.preferences.retry_after_utc) > now
        except ValueError:
            return False

    def _get_with_retry(self, url: str, headers: Mapping[str, str] | None = None) -> HttpResponse:
        last: Exception | None = None
        for attempt in range(3):
            try:
                response = self.client.get(url, headers=headers)
            except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
                last = exc
            else:
                if response.status < 500:
                    return response
                last = UpdateError(f"HTTP {response.status}")
            if attempt < 2:
                self._sleep(0.5 * (2 ** attempt))
        raise last or UpdateError("网络请求失败")

    def _get_metadata(self, url: str) -> bytes:
        ensure_allowed_url(url, prefix=RELEASE_DOWNLOAD_PREFIX)
        response = self._get_with_retry(url)
        if response.status != 200:
            raise UpdateError(f"下载更新元数据失败：HTTP {response.status}")
        if len(response.body) > _MAX_METADATA_BYTES:
            raise UpdateError("更新元数据过大")
        return response.body

    # ---- package download ----------------------------------------------
    def download(
        self,
        manifest: UpdateManifest,
        asset_url: str,
        *,
        on_progress: Callable[[int, int | None], None] | None = None,
        cancel: object | None = None,
    ) -> VerifiedUpdate:
        ensure_allowed_url(asset_url, prefix=RELEASE_DOWNLOAD_PREFIX)
        if not asset_url.endswith("/" + manifest.asset):
            raise UpdateError("下载地址与清单资产名不一致")
        target = self.paths.download_dir(manifest.version) / manifest.asset
        if target.is_file() and target.stat().st_size == manifest.size:
            if file_sha256(target) == manifest.sha256:
                return self._verified(manifest, target)
            target.unlink()
        needed = manifest.size * 2 + _MIN_FREE_BYTES
        target.parent.mkdir(parents=True, exist_ok=True)
        free = _free_bytes(target.parent)
        if free is not None and free < needed:
            raise UpdateError("磁盘空间不足，无法下载并解包更新。")
        last: Exception | None = None
        for attempt in range(_DOWNLOAD_ATTEMPTS):
            try:
                self.client.download(
                    asset_url, target, on_progress=on_progress, cancel=cancel,
                    expected_size=manifest.size,
                )
                return self._verified(manifest, target)
            except DownloadCancelled:
                raise
            except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, ValueError) as exc:
                last = exc
                if attempt + 1 < _DOWNLOAD_ATTEMPTS:
                    self._sleep(1.0)
        raise UpdateError(f"更新下载失败：{last}")

    @staticmethod
    def _verified(manifest: UpdateManifest, package: Path) -> VerifiedUpdate:
        if package.stat().st_size != manifest.size:
            raise UpdateError("下载文件大小与清单不一致")
        if file_sha256(package) != manifest.sha256:
            raise UpdateError("下载文件 SHA-256 与清单不一致")
        return VerifiedUpdate(manifest, package, datetime.now(timezone.utc).isoformat(timespec="seconds"))


def _asset_map(release: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    assets = release.get("assets")
    if not isinstance(assets, list):
        return result
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name, url = asset.get("name"), asset.get("browser_download_url")
        if isinstance(name, str) and isinstance(url, str):
            result[name] = url
    return result


def _asset_url(assets: dict[str, str], name: str) -> str:
    url = assets.get(name)
    if not url:
        raise UpdateError(f"发布缺少资产：{name}")
    ensure_allowed_url(url, prefix=RELEASE_DOWNLOAD_PREFIX)
    return url


def _retry_deadline(response: HttpResponse) -> str:
    from email.utils import parsedate_to_datetime

    retry_after = response.header("Retry-After").strip()
    if retry_after.isdecimal():
        deadline = datetime.now(timezone.utc).timestamp() + int(retry_after)
        return datetime.fromtimestamp(deadline, timezone.utc).isoformat(timespec="seconds")
    if retry_after:
        try:
            parsed = parsedate_to_datetime(retry_after)
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            pass
    reset = response.header("X-RateLimit-Reset").strip()
    if reset.isdecimal():
        return datetime.fromtimestamp(int(reset), timezone.utc).isoformat(timespec="seconds")
    return ""


def _free_bytes(path: Path) -> int | None:
    import shutil

    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None
