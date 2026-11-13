#!/usr/bin/env bash
set +e
echo "=== Fix blinker conflict and finish moto install ==="
pip3 install moto werkzeug flask flask-cors --ignore-installed blinker --no-deps 2>&1
echo ""
echo "=== Install remaining deps ==="
pip3 install boto3 botocore cryptography s3transfer cffi pycparser responses xmltodict requests markupsafe jinja2 itsdangerous blinker 2>&1
echo ""
echo "=== Verify ==="
python3 -c "import moto; print('moto:', moto.__version__)" 2>&1
python3 -c "from moto.server import ThreadedMotoServer; print('ThreadedMotoServer OK')" 2>&1
echo "=== DONE ==="
