#!/usr/bin/env bash
# 测试 MinIO 不同下载源速度（各下载 30 秒然后 kill，看能拉多少）
set +e
echo "=== MinIO Download Speed Test (30s each) ==="
mkdir -p /tmp/minio_test

SOURCES=(
  "https://dl.min.io/server/minio/release/linux-amd64/minio"
  "https://gh-proxy.org/https://github.com/minio/minio/releases/download/latest/minio-linux-amd64"
  "https://dl.min.io/aistor/minio/release/linux-amd64/minio"
)

for url in "${SOURCES[@]}"; do
  echo ""
  echo "--- Testing: $url ---"
  START=$(date +%s)
  timeout 30 wget -q -O /tmp/minio_test/minio_"$RANDOM" "$url" 2>&1 &
  WPID=$!
  sleep 30
  if kill -0 $WPID 2>/dev/null; then
    kill $WPID 2>/dev/null
  fi
  wait $WPID 2>/dev/null
  END=$(date +%s)
  # 找刚下载的文件（最大的）
  SIZE=$(ls -la /tmp/minio_test/ 2>/dev/null | sort -k5 -n | tail -1 | awk '{print $5}')
  if [ "$SIZE" -gt 0 ] 2>/dev/null; then
    SPEED=$(echo "scale=1; $SIZE / 30 / 1024" | bc 2>/dev/null || echo "?")
    echo "Downloaded: ${SIZE} bytes (~${SPEED} KB/s)"
  else
    echo "FAILED (0 bytes)"
  fi
done

rm -rf /tmp/minio_test
echo "=== DONE ==="
