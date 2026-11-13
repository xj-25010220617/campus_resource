#!/bin/bash
# 在 WSL 里下载并启动 MinIO server（Milvus Standalone 依赖）
# 用法：wsl -d trae-ubuntu2 -e bash /mnt/d/实验室204/校园虚拟资源管理/scripts/setup_minio.sh

set -e

export MINIO_BIN=/usr/local/bin/minio
export MINIO_DATA=/data/minio
export MINIO_ACCESS_KEY=minioadmin
export MINIO_SECRET_KEY=minioadmin

if [ -f "$MINIO_BIN" ] && [ -x "$MINIO_BIN" ]; then
    echo "[MinIO] Binary exists: $($MINIO_BIN --version 2>&1 | head -1)"
else
    echo "[MinIO] Downloading..."
    apt-get update -qq && apt-get install -y -qq wget curl 2>/dev/null || true
    # 尝试多个镜像源
    for url in \
        "https://dl.min.io/aistor/minio/release/linux-amd64/minio" \
        "https://github.com/minio/minio/releases/latest/download/minio" \
        "https://mirror.ghproxy.com/https://github.com/minio/minio/releases/latest/download/minio"; do
        echo "  Trying $url ..."
        if curl -fsSL "$url" -o "$MINIO_BIN" 2>/dev/null; then
            break
        fi
    done
    if [ ! -f "$MINIO_BIN" ]; then
        echo "[MinIO] FAILED to download"
        exit 1
    fi
    chmod +x "$MINIO_BIN"
    ls -lh "$MINIO_BIN"
    echo "[MinIO] Installed: $($MINIO_BIN --version 2>&1 | head -1)"
fi

mkdir -p "$MINIO_DATA"

# 停掉可能残留的 MinIO
pkill -f "minio server" 2>/dev/null || true
sleep 1

# 启动 MinIO —— 监听 0.0.0.0:9000 让 Docker 容器也能访问
echo "[MinIO] Starting on port 9000..."
nohup "$MINIO_BIN" server --address ":9000" --console-address ":9001" "$MINIO_DATA" > /tmp/minio.log 2>&1 &
sleep 3

# 检查
if curl -sf http://localhost:9000/minio/health/live > /dev/null 2>&1; then
    echo "[MinIO] HEALTHY"
else
    echo "[MinIO] NOT HEALTHY"
    cat /tmp/minio.log | tail -20
fi

# WSL 对外 IP（Docker 容器在 bridge 网络访问这个）
GATEWAY=$(ip route show default | awk '{print $3}')
echo "[MinIO] WSL IP: $(hostname -I | awk '{print $1}') Gateway: $GATEWAY"
