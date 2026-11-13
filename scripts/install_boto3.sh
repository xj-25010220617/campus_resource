#!/usr/bin/env bash
set -e
echo "=== Install boto3/botocore/s3transfer ==="
pip3 install boto3 botocore s3transfer responses cffi 2>&1
echo ""
echo "=== Verify moto can import ==="
python3 -c "import moto; print('moto:', moto.__version__)" 2>&1
python3 -c "from moto.server import ThreadedMotoServer; print('ThreadedMotoServer OK')" 2>&1
echo "=== ALL GOOD ==="
