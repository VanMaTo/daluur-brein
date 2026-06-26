#!/usr/bin/env python3
# ============================================================
#  DALUUR — LOGBOEK (volledige dump)
#  Toont de ruwe gebeurtenissen van een paar toestellen, zodat we
#  kunnen zien welk datapunt verandert wanneer jij in de app schakelt.
#    1. Zet in de app het toestel een paar keer AAN en UIT.
#    2. Draai dit script meteen erna (handmatig via Actions).
# ============================================================

import os
import time
import json
from tinytuya import Cloud

TUYA_REGION = os.environ.get("TUYA_REGION", "eu")
TUYA_ID     = os.environ["TUYA_CLIENT_ID"].strip()
TUYA_SECRET = os.environ["TUYA_SECRET"].strip()

ONDERZOEK = {
    "filterpomp":      "bff78a994663678c5aki4k",
    "zoutinstallatie": "bf3c1ca3f6090bc3d5jzbw",
}


def main():
    c = Cloud(apiRegion=TUYA_REGION, apiKey=TUYA_ID, apiSecret=TUYA_SECRET)
    now = int(time.time() * 1000)
    start = now - 3 * 60 * 60 * 1000

    for naam, dev in ONDERZOEK.items():
        print(f"================ {naam}  ({dev}) ================")

        # 1) volledige logboek-dump (alle event-types)
        try:
            log = c.getdevicelog(dev, start=start, end=now,
                                 evtype="1,2,3,4,5,6,7,8,9,10", size=100)
            res = log.get("result", {}) if isinstance(log, dict) else {}
            entries = (res.get("logs") or res.get("list") or []) if isinstance(res, dict) else []
            print(f"  logboek: {len(entries)} gebeurtenis(sen)")
            for e in entries:
                print("    " + json.dumps(e, ensure_ascii=False))
            if not entries:
                print(f"    ruwe respons: {log}")
        except Exception as e:
            print(f"  logboek-fout: {e}")

        # 2) huidige ruwe status (alle datapunten, ongefilterd)
        try:
            st = c.getstatus(dev)
            print(f"  status nu: {json.dumps(st, ensure_ascii=False)}")
        except Exception as e:
            print(f"  status-fout: {e}")

        print()


if __name__ == "__main__":
    main()
