#!/bin/bash
cd /home/markvanherf/daluur-brein
set -a
source .env
set +a
/usr/bin/python3 prijzen.py >> /home/markvanherf/daluur-brein/prijzen.log 2>&1
