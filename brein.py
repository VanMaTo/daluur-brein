#!/usr/bin/env python3
# ============================================================
#  DALUUR — BREIN
#  Draait elk uur via GitHub Actions.
#  De toestellen zelf beheer je in  toestellen.py , NIET hier.
#
#  1. leest de planning uit Supabase (of berekent de N goedkoopste)
#  2. beslist of dit Spaanse uur AAN of UIT moet
#  3. bepaalt per toestel de juiste schakelcode (config of auto-detectie)
#  4. voert de cascade uit in volgorde, met pauze tussen de toestellen
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
    """Actieve toestellen, gesorteerd op volgorde (voor AANzetten)."""
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


def codes_from_functions(resp):
    """Codes uit een getfunctions-respons."""
    if not isinstance(resp, dict):
        return []
    res = resp.get("result", {})
    funcs = res.get("functions", []) if isinstance(res, dict) else []
    return [f.get("code") for f in funcs if isinstance(f, dict) and f.get("code")]


def codes_from_status(resp):
    """Codes die het toestel nu rapporteert (getstatus)."""
    out = []
    if isinstance(resp, dict):
        for item in resp.get("result", []) or []:
            if isinstance(item, dict) and item.get("code"):
                out.append(item["code"])
    return out


def codes_from_specs(resp):
    """Codes uit getproperties (specificaties): zowel functions als status."""
    out = []
    if isinstance(resp, dict):
        res = resp.get("result", {})
        if isinstance(res, dict):
            for key in ("functions", "status"):
                for f in (res.get(key) or []):
                    if isinstance(f, dict) and f.get("code"):
                        out.append(f["code"])
    return out


def detecteer_schakelcode(*code_lijsten):
    """Kies de aan/uit-code uit alle gevonden codes: 'switch_1' > 'switch' > eerste 'switch*'."""
    codes = []
    for cl in code_lijsten:
        codes += cl
    for pref in ("switch_1", "switch"):
        if pref in codes:
            return pref
    for c in codes:
        if str(c).startswith("switch"):
            return c
    return None


def switch_value(status, code):
    if isinstance(status, dict):
        for item in status.get("result", []):
            if item.get("code") == code:
                return bool(item.get("value"))
    return None


def set_switch(c, dev_id, code, value):
    return c.sendcommand(dev_id, {"commands": [{"code": code, "value": bool(value)}]})


def cascade(c, value, geordend, info):
    """Schakel de toestellen in de gegeven volgorde; sla toestellen zonder code over."""
    te_schakelen = [t for t in geordend if info[t["naam"]]["code"]]
    if not te_schakelen:
        print("    geen enkel toestel heeft een schakelcode — niets gedaan.")
        return
    for i, t in enumerate(te_schakelen):
        naam = t["naam"]; code = info[naam]["code"]
        print(f"    -> {naam} ({code}) = {value}")
        res = set_switch(c, info[naam]["id"], code, value)
        if tuya_error(res):
            print(f"       FOUT bij {naam}: {res}")
        if i < len(te_schakelen) - 1:
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

    # 2) per toestel: status + functielijst + schakelcode
    c = cloud()
    info = {}
    print("  [TOESTAND + FUNCTIES] per toestel:")
    for t in toestellen:
        st = c.getstatus(t["id"])
        fn = c.getfunctions(t["id"])
        try:
            sp = c.getproperties(t["id"])
        except Exception as e:
            sp = {"Error": str(e)}
        st_codes = codes_from_status(st)
        fn_codes = codes_from_functions(fn)
        sp_codes = codes_from_specs(sp)
        code = t.get("schakelcode") or detecteer_schakelcode(fn_codes, sp_codes, st_codes)
        info[t["naam"]] = {"id": t["id"], "code": code, "status": st}
        print(f"    {t['naam']}:")
        print(f"       status-codes : {st_codes or st.get('result', st) if isinstance(st, dict) else st}")
        print(f"       functie-codes: {fn_codes}")
        print(f"       spec-codes   : {sp_codes}")
        print(f"       spec (ruw)   : {sp.get('result', sp) if isinstance(sp, dict) else sp}")
        print(f"       => schakelcode: {code}")

    if all(tuya_error(i["status"]) for i in info.values()):
        print("  ⚠ Tuya-login mislukt — controleer TUYA_CLIENT_ID / TUYA_SECRET.")
        return

    # 3) schakelen (idempotent: eerste toestel als referentie)
    rep = toestellen[0]["naam"]
    huidig = switch_value(info[rep]["status"], info[rep]["code"])
    print(f"  huidige stand ({rep}): {huidig}")

    if huidig is not None and huidig == moet_aan:
        print("  stand klopt al — geen cascade nodig.")
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
