#!/usr/bin/env bash
set -e
echo "=== Minimal moto install (just S3 mock) ==="
# moto 基础包 + werkzeug + flask 就够 server 模式
pip3 install moto werkzeug flask flask-cors --no-cache-dir 2>&1
echo ""
echo "=== Verify ==="
python3 -c "import moto; print('moto:', moto.__version__)" 2>&1
python3 -c "import werkzeug; print('werkzeug:', werkzeug.__version__)" 2>&1
python3 -c "import flask; print('flask:', flask.__version__)" 2>&1
# moto.server 模块是基础包里就有的，不需要 extras
python3 -c "from moto.server import ThreadedMotoServer; print('ThreadedMotoServer OK')" 2>&1
echo "=== DONE ==="
