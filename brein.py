#!/usr/bin/env python3
# ============================================================
#  DALUUR — BREIN
#  Draait elk uur via GitHub Actions.
#  Toestellen beheer je in  toestellen.py , niet hier.
#
#  1. leest de planning uit Supabase (of berekent de N goedkoopste)
#  2. beslist of dit Spaanse uur AAN of UIT moet
#  3. leest/schakelt per toestel via de v2.0 thing-laag
#  4. logt het werkelijke verbruik per uur naar Supabase
# ============================================================

import os
import sys
import time
import json
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
    """Uurprijzen (eur/kWh) voor 'YYYY-MM-DD'. Eerst REE, dan preciodelaluz als terugval."""
    try:
        return _fetch_ree(datum)
    except Exception as e:
        print(f"  REE-prijzen mislukt ({e}); probeer preciodelaluz...")
        return _fetch_preciodelaluz(datum)


def _fetch_ree(datum):
    url = ("https://apidatos.ree.es/es/datos/mercados/precios-mercados-tiempo-real"
           f"?start_date={datum}T00:00&end_date={datum}T23:59&time_trunc=hour")
    r = requests.get(url, headers={"Accept": "application/json",
                                   "User-Agent": "daluur/1.0 (pool scheduler)"}, timeout=20)
    r.raise_for_status()
    included = r.json().get("included", [])
    serie = next((it for it in included
                  if "PVPC" in str(it.get("attributes", {}).get("title", "") or it.get("type", "")).upper()), None)
    if serie is None and included:
        serie = included[0]
    if serie is None:
        raise ValueError("geen prijsreeks in REE-antwoord")
    out = {}
    for v in serie.get("attributes", {}).get("values", []):
        dt = str(v.get("datetime", ""))
        out[int(dt[11:13])] = float(v["value"]) / 1000.0      # eur/MWh -> eur/kWh
    if len(out) < 23:
        raise ValueError(f"slechts {len(out)} uren van REE")
    return out


def _fetch_preciodelaluz(datum):
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


def schrijf_prijzen(datum, prices):
    """Schrijft de uurprijzen naar Supabase, zodat de app ze daar kan lezen (geen CORS)."""
    try:
        rijen = [{"datum": datum, "uur": int(u), "prijs": round(p, 5)} for u, p in prices.items()]
        requests.post(f"{SUPABASE_URL}/rest/v1/prijzen",
                      headers=_sb_headers({"Prefer": "resolution=merge-duplicates"}),
                      json=rijen, timeout=15)
    except Exception as e:
        print(f"  prijzen wegschrijven mislukt: {e}")


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
    for pref in ("switch_1", "switch", "on_off", "swtich"):
        if pref in codes:
            return pref
    for c in codes:
        if str(c).startswith("switch"):
            return c
    return None


def set_switch(c, dev_id, code, value):
    # v2.0 thing-laag: werkt ook met niet-standaard codes ('swtich', 'on_off', ...)
    body = {"properties": json.dumps({code: bool(value)})}
    res = c.cloudrequest(f"/v2.0/cloud/thing/{dev_id}/shadow/properties/issue", post=body)
    if not tuya_error(res):
        return res
    # terugval: klassieke v1.0 commando-laag (voor standaard toestellen)
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


# ---------- verbruik ----------
def get_meter_state():
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/meter?id=eq.state&select=ts,tellers",
                         headers=_sb_headers(), timeout=15)
        if r.ok and r.json():
            return r.json()[0]
    except Exception as e:
        print(f"  meterstand lezen mislukt: {e}")
    return None


def set_meter_state(ts_iso, tellers):
    try:
        requests.post(f"{SUPABASE_URL}/rest/v1/meter",
                      headers=_sb_headers({"Prefer": "resolution=merge-duplicates"}),
                      json={"id": "state", "ts": ts_iso, "tellers": tellers}, timeout=15)
    except Exception as e:
        print(f"  meterstand schrijven mislukt: {e}")


def add_verbruik(datum, uur, kwh):
    """Telt kwh op bij de bestaande waarde voor dat uur (zodat twee runs samen 1 uur vormen)."""
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/verbruik?datum=eq.{datum}&uur=eq.{uur}&select=kwh",
                         headers=_sb_headers(), timeout=15)
        bestaand = float(r.json()[0]["kwh"] or 0) if (r.ok and r.json()) else 0.0
        nieuw = round(bestaand + kwh, 3)
        requests.post(f"{SUPABASE_URL}/rest/v1/verbruik",
                      headers=_sb_headers({"Prefer": "resolution=merge-duplicates"}),
                      json={"datum": datum, "uur": uur, "kwh": nieuw}, timeout=15)
        return nieuw
    except Exception as e:
        print(f"  verbruik schrijven mislukt: {e}")
        return None


def lees_meetwaarden(c, toestellen):
    """Per toestel met meet_code: de geschaalde meetwaarde (kWh-teller of W)."""
    out = {}
    for t in toestellen:
        code = t.get("meet_code")
        if not code:
            continue
        raw = lees_props(c, t["id"]).get(code)
        if not isinstance(raw, (int, float)):
            continue
        out[t["naam"]] = {"soort": t.get("meet_soort"), "waarde": raw / (t.get("meet_schaal") or 1)}
    return out


def log_verbruik(c, now, toestellen):
    metingen = lees_meetwaarden(c, toestellen)
    if not metingen:
        return

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    state = get_meter_state()
    prev_ts, prev_tellers = None, {}
    if state and state.get("ts"):
        try:
            prev_ts = datetime.datetime.fromisoformat(state["ts"].replace("Z", "+00:00"))
        except Exception:
            prev_ts = None
        prev_tellers = state.get("tellers") or {}
    elapsed_h = (now_utc - prev_ts).total_seconds() / 3600 if prev_ts else 0.0

    kwh = 0.0
    new_tellers = {}
    for naam, m in metingen.items():
        if m["soort"] == "teller":
            cur = m["waarde"]
            new_tellers[naam] = cur
            if prev_ts and naam in prev_tellers:
                prev = prev_tellers[naam]
                kwh += (cur - prev) if cur >= prev else cur     # reset/wrap opvangen
        elif m["soort"] == "vermogen" and elapsed_h > 0:
            kwh += (m["waarde"] / 1000.0) * elapsed_h            # W x tijd -> kWh

    set_meter_state(now_utc.isoformat(), new_tellers)

    if prev_ts:
        kwh = max(0.0, round(kwh, 3))
        datum = now.strftime("%Y-%m-%d")
        tot = add_verbruik(datum, now.hour, kwh)
        print(f"  verbruik: +{kwh} kWh dit interval -> uur {now.hour:02d} totaal {tot} kWh")
    else:
        print("  verbruik: ijkpunt gezet (eerste meting, nog niets weggeschreven)")


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

    # 1) prijzen ophalen en wegschrijven (vandaag + morgen), zodat de app ze leest
    def haal_en_schrijf(d):
        try:
            pr = fetch_prices(d)
            schrijf_prijzen(d, pr)
            return pr
        except Exception as e:
            print(f"  prijzen {d} ophalen mislukt: {e}")
            return None

    prijzen_vandaag = haal_en_schrijf(datum)
    morgen = (now + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    haal_en_schrijf(morgen)

    # 2) planning bepalen
    try:
        plan = get_plan(datum)
    except Exception as e:
        plan = None
        print(f"  planning ophalen mislukt: {e}")

    if plan and isinstance(plan.get("uren"), list):
        uren = [int(x) for x in plan["uren"]]
        bron = "opgeslagen planning"
    elif prijzen_vandaag:
        uren = cheapest_hours(prijzen_vandaag, DEFAULT_N)
        bron = f"{DEFAULT_N} goedkoopste (geen planning)"
        try:
            upsert_plan(datum, DEFAULT_N, uren, handmatig=False)
        except Exception as e:
            print(f"  planning terugschrijven mislukt: {e}")
    else:
        print("  geen planning en geen prijzen -> FAIL-SAFE: niets schakelen.")
        return

    moet_aan = uur in uren
    print(f"  bron: {bron}")
    print(f"  actieve uren: {uren}")
    print(f"  dit uur ({uur:02d}) moet {'AAN' if moet_aan else 'UIT'}")

    # 3) per toestel: stand lezen + code bepalen
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

    # 4) idempotent schakelen
    leesbaar = [t for t in toestellen if isinstance(info[t["naam"]]["stand"], bool)]
    if not any(info[t["naam"]]["code"] for t in toestellen):
        print("  geen schakelcodes bekend — niets te doen.")
    else:
        alle_goed = bool(leesbaar) and all(info[t["naam"]]["stand"] == moet_aan for t in leesbaar)
        if alle_goed:
            print("  alle toestellen staan al goed — geen cascade nodig.")
        else:
            volgorde = toestellen if moet_aan else list(reversed(toestellen))
            print(f"  cascade naar {'AAN' if moet_aan else 'UIT'} ...")
            cascade(c, moet_aan, volgorde, info)
            print("  cascade klaar.")

    # 5) verbruik loggen (mag de schakeling nooit blokkeren)
    try:
        log_verbruik(c, now, toestellen)
    except Exception as e:
        print(f"  verbruik loggen mislukt: {e}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[FOUT] {e}")
        sys.exit(1)
