#!/usr/bin/env python3
# ============================================================
#  DALUUR — INSPECTEUR
#  Toont ALLE toestellen in je Tuya-account: naam, id, categorie,
#  online-status, beschikbare datapunten en een schakelcode-kandidaat.
#  Start dit handmatig via Actions wanneer je de juiste id's of codes
#  van een (nieuw) toestel wil vinden. Schakelt zelf NIETS.
# ============================================================

import os
from tinytuya import Cloud

TUYA_REGION = os.environ.get("TUYA_REGION", "eu")
TUYA_ID     = os.environ["TUYA_CLIENT_ID"].strip()
TUYA_SECRET = os.environ["TUYA_SECRET"].strip()


def codes_status(st):
    out = []
    if isinstance(st, dict):
        for it in st.get("result", []) or []:
            if isinstance(it, dict) and it.get("code"):
                out.append(it["code"])
    return out


def codes_specs(sp):
    out = []
    if isinstance(sp, dict):
        res = sp.get("result", {})
        if isinstance(res, dict):
            for k in ("functions", "status"):
                for f in (res.get(k) or []):
                    if isinstance(f, dict) and f.get("code"):
                        out.append(f["code"])
    return out


def switch_candidate(codes):
    for pref in ("switch_1", "switch"):
        if pref in codes:
            return pref
    for c in codes:
        if str(c).startswith("switch"):
            return c
    return None


def main():
    c = Cloud(apiRegion=TUYA_REGION, apiKey=TUYA_ID, apiSecret=TUYA_SECRET)

    devs = c.getdevices(verbose=True)
    result = devs.get("result", devs) if isinstance(devs, dict) else devs
    if isinstance(result, dict):                 # soms zit de lijst nog een niveau dieper
        result = result.get("devices") or result.get("list") or []

    if not result:
        print("Geen toestellen gevonden of fout bij ophalen:")
        print(devs)
        return

    print(f"[INSPECTEUR] {len(result)} toestel(len) in het account:\n")
    for d in result:
        did    = d.get("id")
        naam   = d.get("name")
        cat    = d.get("category")
        prod   = d.get("product_name") or d.get("product_id")
        online = d.get("online", d.get("is_online"))
        print(f"== {naam}   [{prod}]")
        print(f"   id        : {did}")
        print(f"   categorie : {cat}    online: {online}")
        try:
            st = c.getstatus(did)
            sp = c.getproperties(did)
            codes = sorted(set(codes_status(st) + codes_specs(sp)))
            print(f"   datapunten: {codes}")
            print(f"   schakelcode-kandidaat: {switch_candidate(codes)}")
        except Exception as e:
            print(f"   uitlezen mislukt: {e}")
        print()


if __name__ == "__main__":
    main()
