#!/bin/bash
cd /home/markvanherf/daluur-brein
set -a
source .env
set +a
/usr/bin/python3 brein.py 2>&1 \
  | tee -a /home/markvanherf/daluur-brein/brein.log \
  | /usr/bin/python3 waakhond.py > /dev/null
