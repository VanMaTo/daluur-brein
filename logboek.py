#!/usr/bin/env python3
# ============================================================
#  DALUUR — LOGBOEK
#  Toont de recente gebeurtenissen (datapunt-wijzigingen) van een
#  paar toestellen. Werkwijze:
#    1. Zet in de Smart Life-app het toestel een paar keer AAN en UIT.
#    2. Draai dit script (handmatig via Actions) binnen het uur erna.
#    3. De code die telkens verandert = de echte schakelcode.
# ============================================================

import os
import time
from tinytuya import Cloud

TUYA_REGION = os.environ.get("TUYA_REGION", "eu")
TUYA_ID     = os.environ["TUYA_CLIENT_ID"].strip()
TUYA_SECRET = os.environ["TUYA_SECRET"].strip()

# Toestellen die we willen onderzoeken (naam -> id)
ONDERZOEK = {
    "filterpomp":      "bff78a994663678c5aki4k",
    "zoutinstallatie": "bf3c1ca3f6090bc3d5jzbw",
}


def haal_logs(c, dev, start, end, evtype):
    log = c.getdevicelog(dev, start=start, end=end, evtype=evtype, size=100)
    entries = []
    if isinstance(log, dict):
        res = log.get("result", {})
        if isinstance(res, dict):
            entries = res.get("logs") or res.get("list") or []
    return log, entries


def main():
    c = Cloud(apiRegion=TUYA_REGION, apiKey=TUYA_ID, apiSecret=TUYA_SECRET)
    now = int(time.time() * 1000)
    start = now - 3 * 60 * 60 * 1000          # laatste 3 uur

    for naam, dev in ONDERZOEK.items():
        print(f"== {naam}  ({dev})  — gebeurtenissen laatste 3 uur")
        gevonden = False
        # eerst enkel datapunt-rapporten (event type 7), dan alles als terugval
        for evtype in ("7", "1,2,3,4,5,6,7,8,9,10"):
            try:
                ruw, entries = haal_logs(c, dev, start, now, evtype)
            except Exception as e:
                print(f"   fout bij ophalen (evtype {evtype}): {e}")
                continue
            if entries:
                gevonden = True
                for e in entries:
                    t = e.get("event_time")
                    print(f"   {t}  code = {e.get('code')!r:24}  value = {e.get('value')!r}")
                break
            else:
                print(f"   (geen gebeurtenissen voor evtype {evtype})")
                laatste_ruw = ruw
        if not gevonden:
            print(f"   geen datapunt-gebeurtenissen gevonden.")
            print(f"   ruwe respons: {laatste_ruw}")
        print()


if __name__ == "__main__":
    main()
