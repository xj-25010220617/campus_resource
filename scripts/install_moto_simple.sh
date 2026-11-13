#!/usr/bin/env bash
# 简化 moto 安装（核心包，不装所有 extras）
set +e
echo "=== Installing moto core (no extras) ==="
pip3 install moto 2>&1
echo ""
echo "=== moto version ==="
python3 -c "import moto; print('moto:', moto.__version__)" 2>&1
echo ""
echo "=== Check moto server entry point ==="
which moto_server 2>&1 || python3 -m moto.server --help 2>&1 | head -3 || echo "NO moto_server entry"
echo "=== DONE ==="
