"""Offline tests for signed manifests, release checks and update state."""

import base64
import hashlib
import json
import urllib.error

import pytest

from qx_stc_isp.updates import config as update_config
from qx_stc_isp.updates.checker import UpdateChecker
from qx_stc_isp.updates.config import (
    APP_ID, UpdatePaths, UpdatePreferences, asset_name,
)
from qx_stc_isp.updates.http import DownloadCancelled, HttpResponse
from qx_stc_isp.updates.manifest import (
    UpdateError, UpdateManifest, compare_versions, file_sha256, parse_version, verify_update,
)
from qx_stc_isp.updates.state import (
    ActiveVersion, UpdateJournal, pending_updates, resolve_start_executable,
)

CURRENT = "0.6.0"
TARGET = "0.7.0"


def make_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    return key, public


def make_package(tmp_path, content=b"setup-binary"):
    package = tmp_path / f"{asset_name(TARGET)}"
    package.parent.mkdir(parents=True, exist_ok=True)
    package.write_bytes(content)
    return package


def make_manifest(package, *, version=TARGET, **overrides):
    value = {
        "app_id": APP_ID,
        "channel": "stable",
        "version": version,
        "platform": "win-x64",
        "asset": asset_name(version),
        "size": package.stat().st_size,
        "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        "files_sha256": hashlib.sha256(b"files").hexdigest(),
        "min_assistant": 1,
        "notes": "测试说明",
    }
    value.update(overrides)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign(key, raw):
    return base64.b64encode(key.sign(raw))


class FakeClient:
    def __init__(self, responses, *, download_errors=0):
        self.responses = responses
        self.download_errors = download_errors
        self.download_calls = []
        self.calls = []

    def get(self, url, *, headers=None):
        self.calls.append((url, dict(headers or {})))
        item = self.responses[url]
        if isinstance(item, Exception):
            raise item
        return item

    def download(self, url, destination, *, on_progress=None, cancel=None, expected_size=None):
        self.download_calls.append(url)
        if self.download_errors:
            self.download_errors -= 1
            raise OSError("网络中断")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.responses[url])
        if on_progress is not None:
            on_progress(len(self.responses[url]), expected_size)


def release_payload(version=TARGET, *, prerelease=False):
    base = f"https://github.com/{update_config.RELEASE_REPO}/releases/download/v{version}"
    return {
        "tag_name": f"v{version}",
        "draft": False,
        "prerelease": prerelease,
        "assets": [
            {"name": "update-win-x64.json", "browser_download_url": f"{base}/update-win-x64.json"},
            {"name": "update-win-x64.sig", "browser_download_url": f"{base}/update-win-x64.sig"},
            {"name": asset_name(version), "browser_download_url": f"{base}/{asset_name(version)}"},
        ],
    }


def build_site(tmp_path, key, public, *, version=TARGET, package_bytes=b"setup-binary", **manifest_overrides):
    package = make_package(tmp_path, package_bytes)
    raw = make_manifest(package, version=version, **manifest_overrides)
    signature = sign(key, raw)
    release = release_payload(version)
    urls = {asset["name"]: asset["browser_download_url"] for asset in release["assets"]}
    responses = {
        update_config.LATEST_RELEASE_URL: HttpResponse(
            200, {"ETag": '"abc"'}, json.dumps(release).encode(), update_config.LATEST_RELEASE_URL,
        ),
        urls["update-win-x64.json"]: HttpResponse(200, {}, raw, urls["update-win-x64.json"]),
        urls["update-win-x64.sig"]: HttpResponse(200, {}, signature, urls["update-win-x64.sig"]),
        urls[asset_name(version)]: package_bytes,
    }
    return package, raw, signature, url_start(version), responses


def url_start(version):
    return f"https://github.com/{update_config.RELEASE_REPO}/releases/download/v{version}"


def make_paths(tmp_path):
    return UpdatePaths(tmp_path / "root", tmp_path / "appdata", tmp_path / "local")


# ---- versions and manifests ------------------------------------------------
def test_version_parsing_is_numeric_and_strict():
    assert compare_versions("0.10.0", "0.9.9") == 1
    assert compare_versions("1.2.3", "1.2.3") == 0
    assert parse_version("0.7.0") == (0, 7, 0)
    for bad in ("1.2", "1.2.3.4", "v1.2.3", "01.2.3", "1.2.-3"):
        with pytest.raises(ValueError):
            parse_version(bad)


def test_signed_manifest_verifies_and_tampering_is_rejected(tmp_path):
    key, public = make_key()
    package = make_package(tmp_path)
    raw = make_manifest(package)
    signature = sign(key, raw)
    verified = verify_update(raw, signature, package, public)
    assert verified.manifest.version == TARGET and verified.manifest.min_assistant == 1
    assert verified.package == package

    tampered = raw.replace(b'"notes"', b'"notez"')
    with pytest.raises(UpdateError, match="签名"):
        verify_update(tampered, signature, package, public)
    other_key, other_public = make_key()
    with pytest.raises(UpdateError):
        verify_update(raw, sign(other_key, raw), package, public)
    with pytest.raises(UpdateError, match="未配置"):
        verify_update(raw, signature, package, b"")


def test_manifest_rejects_wrong_app_platform_asset_and_version(tmp_path):
    key, public = make_key()
    package = make_package(tmp_path)
    cases = [
        {"app_id": "com.example.other", "platform": "win-x64", "asset": asset_name(TARGET)},
        {"app_id": APP_ID, "platform": "linux-x64", "asset": asset_name(TARGET)},
        {"app_id": APP_ID, "platform": "win-x64", "asset": "Other-Setup.exe"},
        {"app_id": APP_ID, "platform": "win-x64", "asset": asset_name(TARGET), "channel": "beta"},
    ]
    for overrides in cases:
        raw = make_manifest(package, **overrides)
        with pytest.raises(UpdateError):
            verify_update(raw, sign(key, raw), package, public)


def test_verified_package_size_and_hash_are_checked(tmp_path):
    key, public = make_key()
    package = make_package(tmp_path)
    raw = make_manifest(package)
    signature = sign(key, raw)
    package.write_bytes(b"changed")
    with pytest.raises(UpdateError, match="大小"):
        verify_update(raw, signature, package, public)
    package.write_bytes(b"setup-binary")
    raw = make_manifest(package, sha256="0" * 64)
    with pytest.raises(UpdateError, match="SHA-256"):
        verify_update(raw, sign(key, raw), package, public)


# ---- checker ---------------------------------------------------------------
def test_checker_reports_available_and_caches_manifest_files(tmp_path):
    key, public = make_key()
    paths = make_paths(tmp_path)
    package, raw, signature, base, responses = build_site(tmp_path, key, public)
    checker = UpdateChecker(paths, CURRENT, public_key=public, client=FakeClient(responses))
    result = checker.check(manual=False)
    assert result.status == "available"
    assert result.manifest.version == TARGET
    assert result.asset_url == f"{base}/{asset_name(TARGET)}"
    assert (paths.download_dir(TARGET) / "update-win-x64.json").read_bytes() == raw


def test_checker_filters_prerelease_and_older_versions(tmp_path):
    key, public = make_key()
    paths = make_paths(tmp_path)
    package, raw, signature, base, responses = build_site(tmp_path, key, public)
    release = json.loads(responses[update_config.LATEST_RELEASE_URL].body)
    release["prerelease"] = True
    responses[update_config.LATEST_RELEASE_URL] = HttpResponse(
        200, {}, json.dumps(release).encode(), update_config.LATEST_RELEASE_URL,
    )
    assert UpdateChecker(paths, CURRENT, public_key=public, client=FakeClient(responses)).check().status == "up_to_date"

    release["prerelease"] = False
    release["tag_name"] = "v0.6.0"
    responses[update_config.LATEST_RELEASE_URL] = HttpResponse(
        200, {}, json.dumps(release).encode(), update_config.LATEST_RELEASE_URL,
    )
    assert UpdateChecker(paths, CURRENT, public_key=public, client=FakeClient(responses)).check().status == "up_to_date"


def test_checker_uses_etag_and_reports_304(tmp_path):
    key, public = make_key()
    paths = make_paths(tmp_path)
    client = FakeClient({update_config.LATEST_RELEASE_URL: HttpResponse(
        304, {}, b"", update_config.LATEST_RELEASE_URL)})
    preferences = UpdatePreferences(etag='"abc"')
    checker = UpdateChecker(paths, CURRENT, preferences=preferences, public_key=public, client=client)
    result = checker.check(manual=False)
    assert result.status == "up_to_date"
    assert client.calls[0][1].get("If-None-Match") == '"abc"'


def test_checker_handles_rate_limit_and_retries(tmp_path):
    key, public = make_key()
    paths = make_paths(tmp_path)
    preferences = UpdatePreferences()
    limited = FakeClient({update_config.LATEST_RELEASE_URL: HttpResponse(
        429, {"Retry-After": "120"}, b"", update_config.LATEST_RELEASE_URL)})
    result = UpdateChecker(paths, CURRENT, preferences=preferences,
                           public_key=public, client=limited).check(manual=False)
    assert result.status == "error" and preferences.retry_after_utc
    blocked = UpdateChecker(paths, CURRENT, preferences=preferences,
                            public_key=public, client=limited).check(manual=False)
    assert blocked.status == "error" and not limited.calls[1:]

    _, _, _, _, responses = build_site(tmp_path, key, public)
    attempts = []

    class Flaky:
        def __init__(self):
            self.count = 0

        def get(self, url, *, headers=None):
            self.count += 1
            if self.count < 3:
                raise urllib.error.URLError("offline")
            return responses[url]

    checker = UpdateChecker(paths, CURRENT, public_key=public, client=Flaky(), sleep=lambda _s: attempts.append(1))
    assert checker.check(manual=True).status == "available"
    assert len(attempts) == 2


def test_checker_rejects_unsigned_or_foreign_manifest(tmp_path):
    key, public = make_key()
    paths = make_paths(tmp_path)
    package, raw, signature, base, responses = build_site(tmp_path, key, public)
    responses[f"{base}/update-win-x64.sig"] = HttpResponse(200, {}, base64.b64encode(b"0" * 64), "")
    result = UpdateChecker(paths, CURRENT, public_key=public, client=FakeClient(responses)).check()
    assert result.status == "error" and "签名" in result.message

    release = release_payload(TARGET)
    release["assets"][2]["browser_download_url"] = f"https://evil.example/{asset_name(TARGET)}"
    responses[update_config.LATEST_RELEASE_URL] = HttpResponse(
        200, {}, json.dumps(release).encode(), update_config.LATEST_RELEASE_URL,
    )
    result = UpdateChecker(paths, CURRENT, public_key=public, client=FakeClient(responses)).check()
    assert result.status == "error"


def test_skip_version_only_affects_automatic_checks(tmp_path):
    key, public = make_key()
    paths = make_paths(tmp_path)
    package, raw, signature, base, responses = build_site(tmp_path, key, public)
    preferences = UpdatePreferences(skipped_version=TARGET)
    checker = UpdateChecker(paths, CURRENT, preferences=preferences, public_key=public,
                            client=FakeClient(responses))
    assert checker.check(manual=False).status == "skipped"
    assert checker.check(manual=True).status == "available"


def test_unconfigured_key_never_touches_network(tmp_path):
    client = FakeClient({})
    result = UpdateChecker(make_paths(tmp_path), CURRENT, public_key=b"", client=client).check()
    assert result.status == "unconfigured" and not client.calls


def test_development_version_does_not_compare_as_release(tmp_path):
    key, public = make_key()
    client = FakeClient({})
    result = UpdateChecker(make_paths(tmp_path), "0.7.0.dev4", public_key=public, client=client).check()
    assert result.status == "error" and not client.calls


def test_download_verifies_and_retries(tmp_path):
    key, public = make_key()
    paths = make_paths(tmp_path)
    package, raw, signature, base, responses = build_site(tmp_path, key, public)
    client = FakeClient(responses, download_errors=1)
    checker = UpdateChecker(paths, CURRENT, public_key=public, client=client, sleep=lambda _s: None)
    result = checker.check(manual=True)
    progress = []
    update = checker.download(result.manifest, result.asset_url,
                              on_progress=lambda done, total: progress.append((done, total)))
    assert update.package.read_bytes() == b"setup-binary"
    assert progress and client.download_calls


def test_download_rejects_corruption_cancel_and_low_space(tmp_path, monkeypatch):
    key, public = make_key()
    paths = make_paths(tmp_path)
    package, raw, signature, base, responses = build_site(tmp_path, key, public)
    checker = UpdateChecker(paths, CURRENT, public_key=public, client=FakeClient(responses))
    result = checker.check(manual=True)
    corrupted = FakeClient({**responses, result.asset_url: b"setup-binory"})
    with pytest.raises(UpdateError, match="SHA-256|大小"):
        UpdateChecker(paths, CURRENT, public_key=public, client=corrupted).download(
            result.manifest, result.asset_url)

    class Cancelled(FakeClient):
        def download(self, *args, **kwargs):
            raise DownloadCancelled("cancelled")

    with pytest.raises(DownloadCancelled):
        UpdateChecker(paths, CURRENT, public_key=public, client=Cancelled(responses)).download(
            result.manifest, result.asset_url)

    monkeypatch.setattr("qx_stc_isp.updates.checker._free_bytes", lambda _path: 0)
    with pytest.raises(UpdateError, match="空间"):
        UpdateChecker(paths, CURRENT, public_key=public, client=FakeClient(responses)).download(
            result.manifest, result.asset_url)


# ---- state -----------------------------------------------------------------
def test_runtime_data_uses_install_root_not_the_c_profile(tmp_path, monkeypatch):
    monkeypatch.delenv("ASTRSPIRE_APPDATA", raising=False)
    monkeypatch.delenv("ASTRSPIRE_LOCALAPPDATA", raising=False)
    root = tmp_path / "installed"
    paths = UpdatePaths.for_root(root)
    assert paths.app_data == root / "data" / "app"
    assert paths.local_data == root / "data" / "local"
    monkeypatch.setenv("ASTRSPIRE_APPDATA", str(tmp_path / "override"))
    assert UpdatePaths.for_root(root).app_data == tmp_path / "override"


def test_active_version_and_journal_recover(tmp_path):
    paths = make_paths(tmp_path)
    old_dir = paths.version_dir("0.6.0")
    old_dir.mkdir(parents=True)
    (old_dir / "Astrspire.exe").write_bytes(b"old")
    ActiveVersion("0.6.0").write(paths.active_file)
    assert resolve_start_executable(paths).name == "Astrspire.exe"

    journal = UpdateJournal.create(
        version=TARGET, previous="0.6.0", installer="setup.exe",
        manifest="manifest.json", signature="manifest.sig",
    )
    journal.write(paths.journal_file)
    assert pending_updates(paths).version == TARGET
    assert UpdateJournal.read(paths.journal_file).stage == "waiting"
    journal.with_stage("failed", "boom").write(paths.journal_file)
    assert pending_updates(paths) is None


def test_resolve_falls_back_to_previous(tmp_path):
    paths = make_paths(tmp_path)
    for version in ("0.6.0", "0.7.0"):
        directory = paths.version_dir(version)
        directory.mkdir(parents=True)
        (directory / "Astrspire.exe").write_bytes(version.encode())
    ActiveVersion("0.7.0", "0.6.0").write(paths.active_file)
    paths.version_dir("0.7.0").rename(paths.version_dir("0.7.0.tmp"))
    executable = resolve_start_executable(paths)
    assert executable == paths.version_dir("0.6.0") / "Astrspire.exe"
    assert file_sha256(executable) == hashlib.sha256(b"0.6.0").hexdigest()


def test_resolve_without_active_record_uses_newest_installed_version(tmp_path):
    paths = make_paths(tmp_path)
    assert resolve_start_executable(paths) is None
    for version in ("0.6.0", "0.10.0", "0.7.0", "not-a-version"):
        directory = paths.version_dir(version)
        directory.mkdir(parents=True)
        (directory / "Astrspire.exe").write_bytes(version.encode())
    executable = resolve_start_executable(paths)
    assert executable == paths.version_dir("0.10.0") / "Astrspire.exe"
    paths.active_file.write_text("{broken", encoding="utf-8")
    assert resolve_start_executable(paths) == paths.version_dir("0.10.0") / "Astrspire.exe"
