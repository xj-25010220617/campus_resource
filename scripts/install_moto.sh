#!/usr/bin/env bash
# 安装 moto S3 mock 并测试
set +e
echo "=== Installing moto[s3] ==="
pip3 install 'moto[s3]' 2>&1 | tail -10
echo ""
echo "=== moto version ==="
python3 -c "import moto; print('moto:', moto.__version__)" 2>&1
echo ""
echo "=== Starting moto s3 server on 9000 ==="
# moto_server 是 moto 自带的 S3 mock server
which moto_server 2>&1 || python3 -m moto.server -h 2>&1 | head -5
echo "=== DONE ==="
