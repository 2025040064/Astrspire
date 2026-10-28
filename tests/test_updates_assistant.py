"""Offline tests for the update assistant transaction and version switching."""

import base64
import hashlib
import json

import pytest

from qx_stc_isp.updates import assistant as update_assistant
from qx_stc_isp.updates.config import APP_ID, UpdatePaths, asset_name
from qx_stc_isp.updates.manifest import UpdateError
from qx_stc_isp.updates.state import ActiveVersion, UpdateJournal

TARGET = "0.7.0"


def make_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    key = Ed25519PrivateKey.generate()
    return key, key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )


def build_update(tmp_path, key, *, version=TARGET):
    package = tmp_path / "work" / asset_name(version)
    package.parent.mkdir(parents=True, exist_ok=True)
    package.write_bytes(b"setup")
    value = {
        "app_id": APP_ID, "channel": "stable", "version": version, "platform": "win-x64",
        "asset": asset_name(version), "size": package.stat().st_size,
        "sha256": hashlib.sha256(b"setup").hexdigest(),
        "files_sha256": hashlib.sha256(b"files").hexdigest(),
        "min_assistant": 1, "notes": "",
    }
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    manifest = tmp_path / "work" / "update-win-x64.json"
    signature = tmp_path / "work" / "update-win-x64.sig"
    manifest.write_bytes(raw)
    signature.write_bytes(base64.b64encode(key.sign(raw)))
    return package, manifest, signature, raw


def make_paths(tmp_path):
    return UpdatePaths(tmp_path / "root", tmp_path / "appdata", tmp_path / "local")


def install_fake(paths, version):
    directory = paths.version_dir(version)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "Astrspire.exe").write_bytes(b"new")
    (directory / "build-manifest.json").write_bytes(b"files")


def test_successful_transaction_switches_active_and_restarts(tmp_path, monkeypatch):
    key, public = make_key()
    paths = make_paths(tmp_path)
    old = paths.version_dir("0.6.0")
    old.mkdir(parents=True)
    (old / "Astrspire.exe").write_bytes(b"old")
    ActiveVersion("0.6.0").write(paths.active_file)
    package, manifest, signature, _ = build_update(tmp_path, key)
    restarted = []
    monkeypatch.setattr(update_assistant, "wait_mutex_gone", lambda *_a, **_k: True)
    monkeypatch.setattr(update_assistant, "_run_installer", lambda p, j: install_fake(p, j.version))
    monkeypatch.setattr(update_assistant, "_verify_installed", lambda *a: None)
    monkeypatch.setattr(update_assistant, "_restart", lambda p: restarted.append(p))

    journal = UpdateJournal.create(
        version=TARGET, previous="0.6.0", installer=str(package),
        manifest=str(manifest), signature=str(signature),
    )
    journal.write(paths.journal_file)
    assert update_assistant.execute(paths, journal, public_key=public) == 0
    assert ActiveVersion.read(paths.active_file) == ActiveVersion(TARGET, "0.6.0")
    assert UpdateJournal.read(paths.journal_file).stage == "done"
    assert restarted


def test_waiting_cancel_and_bad_signature_leave_old_version(tmp_path, monkeypatch):
    key, public = make_key()
    paths = make_paths(tmp_path)
    old = paths.version_dir("0.6.0")
    old.mkdir(parents=True)
    (old / "Astrspire.exe").write_bytes(b"old")
    ActiveVersion("0.6.0").write(paths.active_file)
    package, manifest, signature, raw = build_update(tmp_path, key)
    journal = UpdateJournal.create(
        version=TARGET, previous="0.6.0", installer=str(package),
        manifest=str(manifest), signature=str(signature),
    )
    journal.write(paths.journal_file)

    monkeypatch.setattr(update_assistant, "wait_mutex_gone", lambda *_a, **_k: False)
    assert update_assistant.execute(paths, journal, public_key=public) == 1
    failed = UpdateJournal.read(paths.journal_file)
    assert failed.stage == "failed" and "仍在运行" in failed.message
    assert ActiveVersion.read(paths.active_file) == ActiveVersion("0.6.0")

    journal.write(paths.journal_file)
    manifest.write_bytes(raw.replace(b'"notes"', b'"notez"'))
    assert update_assistant.execute(paths, journal, public_key=public) == 1
    assert UpdateJournal.read(paths.journal_file).stage == "failed"


def test_resume_from_verifying_switches_without_reinstalling(tmp_path, monkeypatch):
    key, public = make_key()
    paths = make_paths(tmp_path)
    old = paths.version_dir("0.6.0")
    old.mkdir(parents=True)
    (old / "Astrspire.exe").write_bytes(b"old")
    ActiveVersion("0.6.0").write(paths.active_file)
    package, manifest, signature, _ = build_update(tmp_path, key)
    install_fake(paths, TARGET)
    called = []
    monkeypatch.setattr(update_assistant, "_run_installer", lambda *a: called.append(1))
    monkeypatch.setattr(update_assistant, "_verify_installed", lambda *a: None)
    monkeypatch.setattr(update_assistant, "_restart", lambda p: None)
    journal = UpdateJournal.create(
        version=TARGET, previous="0.6.0", installer=str(package),
        manifest=str(manifest), signature=str(signature),
    ).with_stage("verifying")
    journal.write(paths.journal_file)
    assert update_assistant.execute(paths, journal, public_key=public) == 0
    assert not called
    assert ActiveVersion.read(paths.active_file) == ActiveVersion(TARGET, "0.6.0")


def test_stage_assistant_copies_outside_program_directory(tmp_path):
    paths = make_paths(tmp_path)
    source = tmp_path / "root" / "assistant"
    source.mkdir(parents=True)
    (source / "AstrspireUpdate.exe").write_bytes(b"exe")
    (source / "_internal").mkdir()
    (source / "_internal" / "lib.dat").write_bytes(b"lib")
    staged = update_assistant.stage_assistant(paths, source)
    assert staged.is_file() and paths.root not in staged.parents
    assert (staged.parent / "_internal" / "lib.dat").is_file()


def test_verify_installed_rejects_mismatched_file_manifest(tmp_path):
    key, public = make_key()
    paths = make_paths(tmp_path)
    package, manifest, signature, _ = build_update(tmp_path, key)
    install_fake(paths, TARGET)
    (paths.version_dir(TARGET) / "build-manifest.json").write_bytes(b"other")
    journal = UpdateJournal.create(
        version=TARGET, previous="0.6.0", installer=str(package),
        manifest=str(manifest), signature=str(signature),
    )
    with pytest.raises(UpdateError, match="哈希"):
        update_assistant._verify_installed(paths, journal, public)
