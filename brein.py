#!/usr/bin/env python3
# ============================================================
#  DALUUR — BREIN
#  Draait elk uur via GitHub Actions.
#  Toestellen beheer je in  toestellen.py , niet hier.
#
#  1. leest de planning uit Supabase (of berekent de N goedkoopste)
#  2. beslist of dit Spaanse uur AAN of UIT moet
#  3. leest per toestel de huidige stand via de v2.0 thing-laag
#     (die toont ook niet-standaard codes zoals 'swtich' en 'on_off')
#  4. schakelt in volgorde wat nog niet goed staat, met pauze ertussen
# ============================================================

import os
import sys
import time
import datetime
from zoneinfo import ZoneInfo

import requests
from tinytuya import Cloud

from toestellen import TOESTELLEN

# ---------- instellingen ----------
TZ = ZoneInfo("Europe/Madrid")
DEFAULT_N = 4
PAUZE = 60                              # seconden tussen de toestellen

# ---------- geheimen / omgeving ----------
SUPABASE_URL  = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_ANON = os.environ["SUPABASE_ANON"]
TUYA_REGION   = os.environ.get("TUYA_REGION", "eu")
TUYA_ID       = os.environ["TUYA_CLIENT_ID"].strip()
TUYA_SECRET   = os.environ["TUYA_SECRET"].strip()


def actieve_toestellen():
    actief = [t for t in TOESTELLEN if t.get("actief")]
    return sorted(actief, key=lambda t: t.get("volgorde", 99))


# ---------- Supabase ----------
def _sb_headers(extra=None):
    h = {"apikey": SUPABASE_ANON, "Authorization": f"Bearer {SUPABASE_ANON}",
         "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def get_plan(datum):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/planning?datum=eq.{datum}&select=n,uren",
                     headers=_sb_headers(), timeout=15)
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else None


def upsert_plan(datum, n, uren, handmatig=False):
    requests.post(f"{SUPABASE_URL}/rest/v1/planning",
                  headers=_sb_headers({"Prefer": "resolution=merge-duplicates"}),
                  json={"datum": datum, "n": n, "uren": uren, "handmatig": handmatig,
                        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()},
                  timeout=15)


# ---------- prijzen ----------
def fetch_prices(datum):
    y, m, d = datum.split("-")
    url = f"https://api.preciodelaluz.org/v1/prices/all?zone=PCB&date={d}-{m}-{y}"
    r = requests.get(url, headers={"Accept": "application/json", "User-Agent": "daluur"}, timeout=15)
    r.raise_for_status()
    out = {}
    for info in r.json().values():
        uur = int(str(info["hour"]).split("-")[0])
        p = info["price"]
        out[uur] = p / 1000 if p > 1 else p
    return out


def cheapest_hours(prices, n):
    return sorted(sorted(prices, key=lambda u: prices[u])[:n])


# ---------- Tuya ----------
def cloud():
    return Cloud(apiRegion=TUYA_REGION, apiKey=TUYA_ID, apiSecret=TUYA_SECRET)


def tuya_error(resp):
    return isinstance(resp, dict) and (resp.get("Error") or resp.get("success") is False)


def lees_props(c, dev):
    """Geeft {code: waarde} uit de v2.0 thing-laag (toont ook verborgen codes)."""
    try:
        r = c.cloudrequest(f"/v2.0/cloud/thing/{dev}/shadow/properties")
        if isinstance(r, dict) and isinstance(r.get("result"), dict):
            props = r["result"].get("properties", [])
            return {p.get("code"): p.get("value") for p in props if isinstance(p, dict) and p.get("code")}
        if tuya_error(r):
            print(f"       props-fout: {r}")
    except Exception as e:
        print(f"       props lezen mislukt: {e}")
    return {}


def detecteer_schakelcode(codes):
    """Voor toestellen zonder vaste code: kies een aannemelijke schakelaar."""
    for pref in ("switch_1", "switch", "on_off", "swtich"):
        if pref in codes:
            return pref
    for c in codes:
        if str(c).startswith("switch"):
            return c
    return None


def set_switch(c, dev_id, code, value):
    return c.sendcommand(dev_id, {"commands": [{"code": code, "value": bool(value)}]})


def cascade(c, value, geordend, info):
    te_doen = [t for t in geordend if info[t["naam"]]["code"]]
    for i, t in enumerate(te_doen):
        naam = t["naam"]; code = info[naam]["code"]
        print(f"    -> {naam} ({code}) = {value}")
        res = set_switch(c, info[naam]["id"], code, value)
        if tuya_error(res):
            print(f"       FOUT bij {naam}: {res}")
        if i < len(te_doen) - 1:
            time.sleep(PAUZE)


# ---------- hoofdlogica ----------
def main():
    now = datetime.datetime.now(TZ)
    datum = now.strftime("%Y-%m-%d")
    uur = now.hour
    print(f"[DALUUR-BREIN] {now:%Y-%m-%d %H:%M} (Madrid) — uur {uur}")

    toestellen = actieve_toestellen()
    if not toestellen:
        print("  geen actieve toestellen in toestellen.py — gestopt.")
        return
    print(f"  actieve toestellen: {[t['naam'] for t in toestellen]}")

    # 1) planning
    try:
        plan = get_plan(datum)
    except Exception as e:
        plan = None
        print(f"  planning ophalen mislukt: {e}")

    if plan and isinstance(plan.get("uren"), list):
        uren = [int(x) for x in plan["uren"]]
        bron = "opgeslagen planning"
    else:
        try:
            prices = fetch_prices(datum)
            uren = cheapest_hours(prices, DEFAULT_N)
            bron = f"{DEFAULT_N} goedkoopste (geen planning)"
            try:
                upsert_plan(datum, DEFAULT_N, uren, handmatig=False)
            except Exception as e:
                print(f"  planning terugschrijven mislukt: {e}")
        except Exception as e:
            print(f"  prijzen ophalen mislukt: {e}")
            print("  FAIL-SAFE: niets bekend -> niets schakelen.")
            return

    moet_aan = uur in uren
    print(f"  bron: {bron}")
    print(f"  actieve uren: {uren}")
    print(f"  dit uur ({uur:02d}) moet {'AAN' if moet_aan else 'UIT'}")

    # 2) per toestel: stand lezen + code bepalen
    c = cloud()
    info = {}
    print("  toestellen:")
    for t in toestellen:
        props = lees_props(c, t["id"])
        code = t.get("schakelcode") or detecteer_schakelcode(list(props.keys()))
        stand = props.get(code) if code else None
        info[t["naam"]] = {"id": t["id"], "code": code, "stand": stand}
        leesbaar = "AAN" if stand is True else ("UIT" if stand is False else "?")
        print(f"    {t['naam']}: code={code}  staat nu {leesbaar}")

    # 3) idempotent: alleen schakelen als de leesbare toestellen niet al goed staan
    leesbaar = [t for t in toestellen if isinstance(info[t["naam"]]["stand"], bool)]
    if not any(info[t["naam"]]["code"] for t in toestellen):
        print("  geen schakelcodes bekend — niets te doen.")
        return
    alle_goed = bool(leesbaar) and all(info[t["naam"]]["stand"] == moet_aan for t in leesbaar)

    if alle_goed:
        print("  alle toestellen staan al goed — geen cascade nodig.")
    else:
        volgorde = toestellen if moet_aan else list(reversed(toestellen))
        print(f"  cascade naar {'AAN' if moet_aan else 'UIT'} ...")
        cascade(c, moet_aan, volgorde, info)
        print("  cascade klaar.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[FOUT] {e}")
        sys.exit(1)
