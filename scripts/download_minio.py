#!/usr/bin/env python3
"""在 WSL 里用 Python 下载 MinIO server（比 curl/wget 更可靠）"""
import os
import sys
import time
import urllib.request
import shutil

MINIO_BIN = "/usr/local/bin/minio"
MINIO_URLS = [
    "https://dl.min.io/aistor/minio/release/linux-amd64/minio",
    "https://github.com/minio/minio/releases/latest/download/minio",
    "https://mirror.ghproxy.com/https://github.com/minio/minio/releases/latest/download/minio",
]

def download_with_progress(url, dest):
    print(f"  Downloading {url} -> {dest}")
    start = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MinIO-Installer/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = resp.headers.get("Content-Length")
            if total:
                total = int(total)
            downloaded = 0
            chunk_size = 64 * 1024
            with open(dest + ".part", "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded * 100 // total
                        elapsed = time.time() - start
                        print(f"\r    {pct}% ({downloaded//1024//1024}MB/{total//1024//1024}MB) {elapsed:.1f}s", end="")
            shutil.move(dest + ".part", dest)
            os.chmod(dest, 0o755)
            elapsed = time.time() - start
            size = os.path.getsize(dest)
            print(f"\n  Done: {size//1024//1024}MB in {elapsed:.1f}s")
            return True
    except Exception as e:
        print(f"\n  Failed: {e}")
        if os.path.exists(dest + ".part"):
            os.remove(dest + ".part")
        return False

if os.path.exists(MINIO_BIN) and os.path.getsize(MINIO_BIN) > 10_000_000:
    print(f"[MinIO] Already installed ({os.path.getsize(MINIO_BIN)//1024//1024}MB)")
else:
    print("[MinIO] Python downloader starting...")
    for url in MINIO_URLS:
        if download_with_progress(url, MINIO_BIN):
            break
    else:
        print("[MinIO] All URLs failed")
        sys.exit(1)

# 验证
if os.path.exists(MINIO_BIN):
    size = os.path.getsize(MINIO_BIN)
    print(f"[MinIO] Binary size: {size//1024//1024}MB")
    # 检查是不是真的 binary
    with open(MINIO_BIN, "rb") as f:
        magic = f.read(4)
    if magic[:2] == b"\x7fELF":
        print("[MinIO] Valid ELF binary ✓")
    else:
        print(f"[MinIO] WARNING: magic bytes = {magic.hex()} (may be HTML/error page)")
