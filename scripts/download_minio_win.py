#!/usr/bin/env python3
"""在 Windows 侧下载 MinIO server EXE（走 Windows 网络层，更快）"""
import os, sys, time, urllib.request

URL = "https://dl.min.io/aistor/minio/release/windows-amd64/minio.exe"
DEST = r"C:\Users\Aurora\AppData\Local\Temp\minio.exe"

print(f"Downloading {URL} -> {DEST}")
start = time.time()
try:
    req = urllib.request.Request(URL, headers={"User-Agent": "MinIO-Installer"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        total = resp.headers.get("Content-Length")
        total = int(total) if total else None
        print(f"  Size: {total//1024//1024 if total else 'unknown'}MB")
        
        downloaded = 0
        chunk_size = 256 * 1024  # 256KB chunks
        with open(DEST + ".part", "wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                elapsed = time.time() - start
                if total:
                    pct = downloaded * 100 // total
                    mbps = (downloaded / 1024 / 1024) / max(elapsed, 1)
                    eta = (total - downloaded) / (downloaded / max(elapsed, 1)) / 60 if downloaded > 0 else 0
                    print(f"\r  {pct}% ({downloaded//1024//1024}/{total//1024//1024}MB) {mbps:.1f}MB/s ETA {eta:.0f}min", end="")
                elif downloaded % (5*1024*1024) == 0:
                    print(f"\r  {downloaded//1024//1024}MB in {elapsed:.0f}s", end="")
        import shutil
        shutil.move(DEST + ".part", DEST)
        os.chmod(DEST, 0o755)
        elapsed = time.time() - start
        size = os.path.getsize(DEST)
        print(f"\n  Done: {size//1024//1024}MB in {elapsed:.1f}s ({size/1024/1024/elapsed:.1f}MB/s)")
except Exception as e:
    print(f"\n  FAILED: {e}")
    if os.path.exists(DEST + ".part"):
        os.remove(DEST + ".part")
    sys.exit(1)
