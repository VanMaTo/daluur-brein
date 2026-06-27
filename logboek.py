#!/usr/bin/env python3
# ============================================================
#  DALUUR — DIEPE INSPECTIE
#  Vraagt de diepere "thing model"-lagen (v2.0) op, die meestal
#  ook de verborgen/ruwe datapunten tonen — inclusief de schakelaar.
# ============================================================

import os
import json
from tinytuya import Cloud

TUYA_REGION = os.environ.get("TUYA_REGION", "eu")
TUYA_ID     = os.environ["TUYA_CLIENT_ID"].strip()
TUYA_SECRET = os.environ["TUYA_SECRET"].strip()

ONDERZOEK = {
    "warmtepomp":      "bff1ab1d1427ccdd9ag0z4",
    "filterpomp":      "bff78a994663678c5aki4k",
    "zoutinstallatie": "bf3c1ca3f6090bc3d5jzbw",
}


def vraag(c, label, url):
    print(f"  --- {label} ---")
    try:
        r = c.cloudrequest(url)
        print("  " + json.dumps(r, ensure_ascii=False))
    except Exception as e:
        print(f"  fout: {e}")


def main():
    c = Cloud(apiRegion=TUYA_REGION, apiKey=TUYA_ID, apiSecret=TUYA_SECRET)
    for naam, dev in ONDERZOEK.items():
        print(f"================ {naam}  ({dev}) ================")
        # alle eigenschappen met hun ruwe datapunt-id en code + huidige waarde
        vraag(c, "shadow/properties (v2.0)", f"/v2.0/cloud/thing/{dev}/shadow/properties")
        # het volledige datamodel: alle gedefinieerde datapunten van het toestel
        vraag(c, "model (v2.0)", f"/v2.0/cloud/thing/{dev}/model")
        print()


if __name__ == "__main__":
    main()
