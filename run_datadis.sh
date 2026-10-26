#!/bin/bash
cd /home/markvanherf/daluur-brein
set -a
source .env
set +a
timeout --signal=TERM --kill-after=30 900 /usr/bin/python3 datadis.py >> /home/markvanherf/daluur-brein/datadis.log 2>&1
