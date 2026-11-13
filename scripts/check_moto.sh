#!/usr/bin/env bash
python3 -c "import moto; print('moto:', moto.__version__)" 2>&1
python3 -c "from moto.server import ThreadedMotoServer; print('ThreadedMotoServer OK')" 2>&1
python3 -c "import boto3; print('boto3:', boto3.__version__)" 2>&1
