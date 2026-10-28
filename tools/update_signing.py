"""Ed25519 key and update-manifest signing helper.

The private key never enters the source tree, the application bundle or logs.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qx_stc_isp.updates.manifest import UpdateError, verify_update  # noqa: E402


def _public_b64(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def keygen(path: Path, *, force: bool = False) -> str:
    if path.exists() and not force:
        raise SystemExit(f"拒绝覆盖已有私钥：{path}（使用 --force 明确覆盖）")
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    path.write_bytes(pem)
    if os.name == "posix":
        os.chmod(path, 0o600)
    print(f"私钥已写入 {path}；请离线备份，切勿提交或随包发布。")
    public = _public_b64(key)
    print("将以下公钥填入 src/qx_stc_isp/updates/config.py 的 TRUSTED_PUBLIC_KEY_B64：")
    print(public)
    return public


def load_private(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit("私钥不是 Ed25519")
    return key


def sign(private: Path, manifest: Path, output: Path) -> None:
    raw = manifest.read_bytes()
    signature = load_private(private).sign(raw)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(base64.b64encode(signature))
    print(f"已签名 {manifest} -> {output}")


def verify(public_b64: str, manifest: Path, signature: Path, package: Path | None) -> None:
    key = base64.b64decode(public_b64, validate=True)
    if package is None:
        from qx_stc_isp.updates.manifest import UpdateManifest, decode_signature, verify_signature

        verify_signature(manifest.read_bytes(), decode_signature(signature.read_bytes()), key)
        print("签名有效：", UpdateManifest.parse(manifest.read_bytes()).version)
        return
    update = verify_update(manifest.read_bytes(), signature.read_bytes(), package, key)
    print(f"验证通过：{update.manifest.version} {update.package.name} {update.manifest.size} bytes")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="update_signing")
    sub = parser.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("keygen", help="生成 Ed25519 密钥对")
    generate.add_argument("--private", required=True, type=Path)
    generate.add_argument("--force", action="store_true")

    signing = sub.add_parser("sign", help="对清单原始字节签名")
    signing.add_argument("--private", type=Path, default=Path(os.environ.get("ASTRSPIRE_UPDATE_KEY", "")))
    signing.add_argument("--manifest", required=True, type=Path)
    signing.add_argument("--out", required=True, type=Path)

    checking = sub.add_parser("verify", help="验证签名与安装包")
    checking.add_argument("--public", required=True)
    checking.add_argument("--manifest", required=True, type=Path)
    checking.add_argument("--signature", required=True, type=Path)
    checking.add_argument("--package", type=Path)

    args = parser.parse_args(argv)
    if args.command == "keygen":
        keygen(args.private, force=args.force)
    elif args.command == "sign":
        if not str(args.private):
            parser.error("缺少 --private 或 ASTRSPIRE_UPDATE_KEY")
        sign(args.private, args.manifest, args.out)
    else:
        try:
            verify(args.public, args.manifest, args.signature, args.package)
        except (UpdateError, OSError, ValueError) as exc:
            print(f"验证失败：{exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
