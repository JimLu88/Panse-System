"""Publish final product dimension deliverables to the NAS Panse ERP.

The editable source remains in Synology Drive.  ERP receives only the final
PNG and UTF-8 text note (plus SVG/JSON masters kept server-side for versioned
history).  A local content hash makes scheduled runs no-ops when nothing has
changed.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

from generate_dimension_final_texts import build_text


ASSET_ROOT = Path(r"D:\SynologyDrive\2026\尺寸图_矢量编辑")
SSH = Path(r"C:\Windows\System32\OpenSSH\ssh.exe")
SCP = Path(r"C:\Windows\System32\OpenSSH\scp.exe")
SSH_KEY = Path(r"C:\Users\lzdwy\.ssh\panse_nas")
NAS_TARGET = "15068803006@192.168.31.21"
NAS_PORT = "2222"
NAS_STAGE = "/volume1/homes/15068803006/stage"
NAS_SOURCE = "/volume1/docker/panse/storage/dimension_publish_source"
APP_STATE = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\lzdwy\AppData\Local")) / "PanseSystem"
MANIFEST_PATH = APP_STATE / "dimension_final_publish_state.json"
STATUS_PATH = APP_STATE / "dimension_final_publish_status.json"
LOCK_PATH = APP_STATE / "dimension_final_publish.lock"


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def write_status(status: str, message: str, **extra: object) -> None:
    atomic_json(STATUS_PATH, {
        "status": status,
        "message": message,
        "checked_at": datetime.now().astimezone().isoformat(),
        **extra,
    })


def acquire_lock() -> int:
    APP_STATE.mkdir(parents=True, exist_ok=True)
    try:
        return os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        age = datetime.now().timestamp() - LOCK_PATH.stat().st_mtime
        if age < 30 * 60:
            raise SystemExit("已有尺寸发布任务正在运行")
        LOCK_PATH.unlink(missing_ok=True)
        return os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)


def render_preview_if_needed(svg: Path, preview: Path) -> bool:
    if preview.exists() and preview.stat().st_mtime >= svg.stat().st_mtime:
        return False
    import resvg_py

    font_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"
    png = resvg_py.svg_to_bytes(
        svg_string=svg.read_text(encoding="utf-8"),
        font_dirs=[str(font_dir)] if font_dir.is_dir() else None,
        background="#ffffff",
    )
    fd, temp_name = tempfile.mkstemp(prefix="dimension-preview-", suffix=".png", dir=preview.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        temp.write_bytes(bytes(png))
        os.replace(temp, preview)
    finally:
        temp.unlink(missing_ok=True)
    return True


def collect_files(root: Path) -> tuple[list[Path], list[str], int]:
    index_path = root / "manual_asset_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    products = index.get("products") or []
    if len(products) != 39:
        raise RuntimeError(f"资产索引应为 39 份，实际 {len(products)}")

    files = [index_path]
    regenerated = 0
    missing: list[str] = []
    for item in products:
        name = str(item["product"])
        svg = root / f"{name}.svg"
        metadata = root / f"{name}.dimensions.json"
        preview = root / f"{name}_preview.png"
        note = root / f"{name}_说明.txt"
        if not note.exists():
            note.write_text(build_text(item), encoding="utf-8")
        if svg.exists() and render_preview_if_needed(svg, preview):
            regenerated += 1
        for path in (svg, metadata, preview, note):
            if not path.is_file():
                missing.append(str(path))
            else:
                files.append(path)
    return files, missing, regenerated


def content_hash(files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.name):
        digest.update(path.name.encode("utf-8"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, capture_output=True, timeout=timeout)


def publish(files: list[Path], digest: str, root: Path) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_name = f"panse-dimension-final-{stamp}.tar.gz"
    local_archive = Path(tempfile.gettempdir()) / archive_name
    remote_archive = f"{NAS_STAGE}/{archive_name}"
    try:
        with tarfile.open(local_archive, "w:gz") as bundle:
            for path in files:
                bundle.add(path, arcname=path.name, recursive=False)
        archive_hash = hashlib.sha256(local_archive.read_bytes()).hexdigest()
        run([
            str(SCP), "-O", "-i", str(SSH_KEY), "-P", NAS_PORT,
            str(local_archive), f"{NAS_TARGET}:{remote_archive}",
        ])
        remote_script = f"""set -eu
archive='{remote_archive}'
actual=$(sha256sum "$archive" | awk '{{print $1}}')
test "$actual" = '{archive_hash}'
sudo -n mkdir -p '{NAS_SOURCE}'
sudo -n tar -xzf "$archive" -C '{NAS_SOURCE}'
sudo -n /usr/local/bin/docker exec panse-system-api-1 python scripts/import_product_dimensions.py --asset-root /app/storage/dimension_publish_source
rm -f "$archive"
"""
        encoded = base64.b64encode(remote_script.encode("utf-8")).decode("ascii")
        result = run([
            str(SSH), "-i", str(SSH_KEY), "-p", NAS_PORT, NAS_TARGET,
            f"echo {encoded} | base64 -d | sh",
        ])
        output = (result.stdout + result.stderr).strip()
        atomic_json(MANIFEST_PATH, {
            "content_hash": digest,
            "published_at": datetime.now().astimezone().isoformat(),
            "asset_root": str(root),
            "file_count": len(files),
        })
        return output
    finally:
        local_archive.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="发布最终尺寸图片和文字说明到畔色 ERP")
    parser.add_argument("--asset-root", type=Path, default=ASSET_ROOT)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    lock_fd = acquire_lock()
    try:
        root = args.asset_root.resolve()
        files, missing, regenerated = collect_files(root)
        if missing:
            raise RuntimeError("缺少最终发布文件：\n" + "\n".join(missing))
        digest = content_hash(files)
        previous = {}
        if MANIFEST_PATH.is_file():
            previous = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        if not args.force and previous.get("content_hash") == digest:
            write_status("no_change", "最终尺寸文件没有变化", content_hash=digest)
            print(json.dumps({"ok": True, "changed": False, "regenerated_previews": regenerated}, ensure_ascii=False))
            return 0
        if args.dry_run:
            print(json.dumps({
                "ok": True,
                "changed": True,
                "file_count": len(files),
                "regenerated_previews": regenerated,
                "content_hash": digest,
            }, ensure_ascii=False))
            return 0
        output = publish(files, digest, root)
        write_status("published", "最终尺寸图片和文字说明已更新到 ERP", content_hash=digest, output=output[-4000:])
        print(output)
        return 0
    except Exception as exc:
        write_status("error", str(exc))
        raise
    finally:
        os.close(lock_fd)
        LOCK_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
