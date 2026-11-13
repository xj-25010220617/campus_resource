#!/usr/bin/env bash
set +e
echo "=== pip install moto[server] ==="
pip3 install "moto[server]" 2>&1
EXIT=$?
echo ""
echo "=== EXIT_CODE=$EXIT ==="
echo ""
if [ $EXIT -eq 0 ]; then
  echo "=== Verify moto ==="
  pip3 show moto 2>&1 | head -3
  python3 -c "import moto; print('moto:', moto.__version__)" 2>&1
  which moto_server 2>&1
  moto_server --help 2>&1 | head -5 || echo "no moto_server cmd"
fi
echo "=== ALL DONE ==="
