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
WP_POLL = 25                            # seconden tussen 'is de WP al online?'-checks
WP_MAX_WACHT = 240                      # max. seconden wachten tot de WP online komt

# ---------- geheimen / omgeving ----------
SUPABASE_URL  = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_ANON = os.environ["SUPABASE_ANON"]
TUYA_REGION   = os.environ.get("TUYA_REGION", "eu")
TUYA_ID       = os.environ["TUYA_CLIENT_ID"].strip()
TUYA_SECRET   = os.environ["TUYA_SECRET"].strip()
ESIOS_TOKEN   = os.environ.get("ESIOS_TOKEN", "").strip()
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()


# ---------- telegram ----------
def stuur_telegram(tekst):
    """Stuurt een Telegram-bericht. Ontbrekende instellingen of een falende
    request mogen het brein nooit doen crashen -> stil overslaan/loggen."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": tekst},
            timeout=10,
        )
        if not r.ok:
            print(f"  telegram-bericht mislukt: {r.status_code} {r.text}")
    except Exception as e:
        print(f"  telegram-bericht mislukt: {e}")


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


def get_plan(datum, groep):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/planning?datum=eq.{datum}&groep=eq.{groep}&select=n,uren,handmatig",
                     headers=_sb_headers(), timeout=15)
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else None


def upsert_plan(datum, groep, n, uren, handmatig=False):
    requests.post(f"{SUPABASE_URL}/rest/v1/planning",
                  headers=_sb_headers({"Prefer": "resolution=merge-duplicates"}),
                  json={"datum": datum, "groep": groep, "n": n, "uren": uren, "handmatig": handmatig,
                        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()},
                  timeout=15)


def bepaal_uren(groep, datum, prijzen):
    """Uren voor één groep op één dag. Handmatige keuze is heilig; anders
    herberekenen op de actuele prijzen (automatiek beweegt mee)."""
    try:
        plan = get_plan(datum, groep)
    except Exception as e:
        print(f"    planning ophalen ({groep}) mislukt: {e}")
        plan = None
    if plan and isinstance(plan.get("uren"), list) and plan.get("handmatig"):
        return [int(x) for x in plan["uren"]], "handmatig"
    if prijzen:
        n = plan.get("n") if (plan and isinstance(plan.get("n"), int)) else DEFAULT_N
        if not isinstance(n, int) or n < 0 or n > 24:
            n = DEFAULT_N
        uren = cheapest_hours(prijzen, n)
        try:
            upsert_plan(datum, groep, n, uren, handmatig=False)
        except Exception as e:
            print(f"    planning terugschrijven ({groep}) mislukt: {e}")
        return uren, f"{n} goedkoopste (auto)"
    if plan and isinstance(plan.get("uren"), list):
        return [int(x) for x in plan["uren"]], "opgeslagen (geen verse prijzen)"
    return None, "onbekend"


# ---------- prijzen ----------
def fetch_prices(datum):
    """Uurprijzen (eur/kWh, ECHTE PVPC) voor 'YYYY-MM-DD'.
    Probeert de bronnen in volgorde en neemt de eerste die een geldige,
    volledige PVPC-set geeft:
      1. ESIOS  — de officiële bron (indicator 1001), heeft de dag-vooruit
                  rond 20:20 al klaar; werkt op de Pi. Vereist ESIOS_TOKEN.
      2. REE    — apidatos, enkel de PVPC-reeks (nooit de spotprijs).
      3. preciodelaluz — laatste redmiddel (op sommige netwerken DNS-geblokkeerd).
    """
    fouten = []
    for naam, bron in (("ESIOS", _fetch_esios_pvpc),
                       ("REE", _fetch_ree_pvpc),
                       ("preciodelaluz", _fetch_preciodelaluz)):
        try:
            return bron(datum)
        except Exception as e:
            fouten.append(f"{naam}: {e}")
    raise ValueError("geen enkele prijsbron gaf een geldige PVPC-set — " + " | ".join(fouten))


def _fetch_esios_pvpc(datum):
    if not ESIOS_TOKEN:
        raise ValueError("geen ESIOS_TOKEN ingesteld")
    url = (f"https://api.esios.ree.es/indicators/1001"
           f"?start_date={datum}T00:00&end_date={datum}T23:59")
    headers = {"Accept": "application/json; application/vnd.esios-api-v1+json",
               "Content-Type": "application/json",
               "x-api-key": ESIOS_TOKEN}
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    waarden = r.json().get("indicator", {}).get("values", [])
    out = {}
    for v in waarden:
        if v.get("geo_id") != 8741:            # 8741 = Península (PCB)
            continue
        dt = str(v.get("datetime", ""))        # bv. 2026-07-04T00:00:00.000+02:00
        out[int(dt[11:13])] = float(v["value"]) / 1000.0   # eur/MWh -> eur/kWh
    if len(out) < 23:
        raise ValueError(f"slechts {len(out)} PVPC-uren van ESIOS")
    return out


def _fetch_preciodelaluz(datum):
    y, m, d = datum.split("-")
    url = f"https://api.preciodelaluz.org/v1/prices/all?zone=PCB&date={d}-{m}-{y}"
    r = requests.get(url, headers={"Accept": "application/json", "User-Agent": "daluur/1.0"}, timeout=15)
    r.raise_for_status()
    out = {}
    for info in r.json().values():
        if not isinstance(info, dict) or "hour" not in info:
            continue
        uur = int(str(info["hour"]).split("-")[0])
        p = float(info["price"])
        out[uur] = p / 1000 if p > 1 else p     # soms eur/MWh, soms al eur/kWh
    if len(out) < 23:
        raise ValueError(f"slechts {len(out)} uren van preciodelaluz")
    return out


def _fetch_ree_pvpc(datum):
    url = ("https://apidatos.ree.es/es/datos/mercados/precios-mercados-tiempo-real"
           f"?start_date={datum}T00:00&end_date={datum}T23:59&time_trunc=hour")
    r = requests.get(url, headers={"Accept": "application/json",
                                   "User-Agent": "daluur/1.0 (pool scheduler)"}, timeout=20)
    r.raise_for_status()
    included = r.json().get("included", [])
    # ENKEL de PVPC-reeks pakken — nooit terugvallen op de spotprijs
    serie = next((it for it in included
                  if "PVPC" in str(it.get("attributes", {}).get("title", "") or it.get("type", "")).upper()), None)
    if serie is None:
        raise ValueError("geen PVPC-reeks (enkel spot?)")
    out = {}
    for v in serie.get("attributes", {}).get("values", []):
        dt = str(v.get("datetime", ""))
        out[int(dt[11:13])] = float(v["value"]) / 1000.0      # eur/MWh -> eur/kWh
    if len(out) < 23:
        raise ValueError(f"slechts {len(out)} PVPC-uren van REE")
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


def toestel_online(c, dev, code):
    """True als het toestel bereikbaar is (zijn schakelcode is uitleesbaar)."""
    props = lees_props(c, dev)
    return isinstance(props.get(code), bool) if code else bool(props)


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
    # morgen pas ophalen vanaf ~20:15 (REE publiceert de dag-vooruit prijzen rond dan)
    if (now.hour, now.minute) >= (20, 15):
        morgen = (now + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        prijzen_morgen = haal_en_schrijf(morgen)
        if prijzen_morgen:
            goedkoopste = cheapest_hours(prijzen_morgen, DEFAULT_N)
            uren_txt = ", ".join(f"{u:02d}u" for u in goedkoopste)
            stuur_telegram(f"☀️ Daluur: prijzen voor {morgen} zijn binnen.\nGoedkoopste uren: {uren_txt}")

    # 2) planning per GROEP bepalen (elke groep = eigen schema)
    groepen = sorted(set(t.get("groep", "circulatie") for t in toestellen))
    groep_uren = {}
    print("  planning per groep:")
    for g in groepen:
        u, bron = bepaal_uren(g, datum, prijzen_vandaag)
        groep_uren[g] = u
        toon = u if u is not None else "onbekend"
        print(f"    {g}: {bron} -> {toon}")

    # 3) doel per toestel bepalen
    #    - basis: staat dit uur in het schema van zijn groep?
    #    - 'hangt_af_van': draait de groep waarvan dit toestel stroom krijgt
    #      (bv. de WP hangt aan de circulatie via de flow-teleruptor)? Zo niet,
    #      dan is het toestel stroomloos -> overslaan (niet lezen, niet schakelen).
    doel = {}
    te_beheren = []
    for t in toestellen:
        g = t.get("groep", "circulatie")
        g_uren = groep_uren.get(g)
        if g_uren is None:
            print(f"    {t['naam']}: schema ({g}) onbekend -> met rust gelaten")
            continue
        dep = t.get("hangt_af_van")
        if dep:
            dep_uren = groep_uren.get(dep)
            if dep_uren is None or uur not in dep_uren:
                print(f"    {t['naam']}: {dep} draait dit uur niet -> stroomloos, overgeslagen")
                continue
        doel[t["naam"]] = uur in g_uren
        te_beheren.append(t)

    if not te_beheren:
        print("  niets te beheren dit uur.")
        return

    # 4) standen lezen + code bepalen
    c = cloud()
    info = {}
    print("  toestellen:")
    for t in te_beheren:
        props = lees_props(c, t["id"])
        code = t.get("schakelcode") or detecteer_schakelcode(list(props.keys()))
        stand = props.get(code) if code else None
        info[t["naam"]] = {"id": t["id"], "code": code, "stand": stand}
        leesbaar = "AAN" if stand is True else ("UIT" if stand is False else "?")
        wil = "AAN" if doel[t["naam"]] else "UIT"
        print(f"    {t['naam']}: code={code}  staat nu {leesbaar}  moet {wil}")

    # 5) schakelen
    beheerbaar = [t for t in te_beheren if info[t["naam"]]["code"]]
    gewone   = [t for t in beheerbaar if not t.get("hangt_af_van")]
    flow_afh = [t for t in beheerbaar if t.get("hangt_af_van")]   # bv. de WP
    mislukt = []   # (naam, reden) — voor het Telegram-alertbericht op het einde

    # 5a) gewone toestellen (circulatie & zout): UIT eerst in omgekeerde volgorde,
    #     dan AAN oplopend. Onleesbaar telt als 'klopt niet' -> schakelen (fail-safe).
    uit = [t for t in gewone if not doel[t["naam"]] and info[t["naam"]]["stand"] is not False]
    aan = [t for t in gewone if doel[t["naam"]] and info[t["naam"]]["stand"] is not True]
    ops = ([(t, False) for t in sorted(uit, key=lambda t: t.get("volgorde", 99), reverse=True)]
           + [(t, True) for t in sorted(aan, key=lambda t: t.get("volgorde", 99))])
    if not ops:
        print("  circulatie: staat al goed.")
    else:
        for i, (t, val) in enumerate(ops):
            naam = t["naam"]; code = info[naam]["code"]
            print(f"    -> {naam} ({code}) = {val}")
            res = set_switch(c, info[naam]["id"], code, val)
            if tuya_error(res):
                print(f"       FOUT bij {naam}: {res}")
                mislukt.append((naam, "schakelfout"))
            if i < len(ops) - 1:
                time.sleep(PAUZE)

    # 5b) flow-afhankelijke toestellen (WP): PAS NU, want de circulatie is nu geregeld.
    #     De WP komt (of blijft) hierdoor online. We wachten actief tot hij bereikbaar
    #     is en zetten hem dan in de gewenste stand — of dat nu AAN of UIT is. Zo kan
    #     hij nooit 'blijven hangen' in zijn vorige stand na een stroomonderbreking.
    for t in flow_afh:
        naam = t["naam"]; code = info[naam]["code"]; wil = doel[naam]
        gewacht = 0
        online = toestel_online(c, t["id"], code)
        while not online and gewacht < WP_MAX_WACHT:
            print(f"       {naam} nog niet online, wacht… ({gewacht}s)")
            time.sleep(WP_POLL); gewacht += WP_POLL
            online = toestel_online(c, t["id"], code)
        if not online:
            print(f"       {naam} bleef offline na {gewacht}s — overgeslagen, volgende run opnieuw")
            mislukt.append((naam, f"niet online gekomen na {gewacht}s"))
            continue
        stand = lees_props(c, t["id"]).get(code)
        if stand is wil:
            print(f"       {naam} staat al goed ({'AAN' if wil else 'UIT'})")
            continue
        print(f"       {naam} online na {gewacht}s  ->  {wil}")
        res = set_switch(c, t["id"], code, wil)
        if tuya_error(res):
            print(f"       FOUT bij {naam}: {res}")
            mislukt.append((naam, "schakelfout"))

    print("  schakelen klaar.")

    if mislukt:
        regels = "\n".join(f"• {naam}: {reden}" for naam, reden in mislukt)
        stuur_telegram(f"⚠️ Daluur: probleem bij het schakelen (uur {uur:02d}):\n{regels}")

    # 6) verbruik loggen (mag de schakeling nooit blokkeren)
    try:
        # meet ALLE toestellen met een meet_code (ook inactieve, zoals de WP
        # die via de flow-teleruptor draait maar niet door ons geschakeld wordt)
        log_verbruik(c, now, TOESTELLEN)
    except Exception as e:
        print(f"  verbruik loggen mislukt: {e}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[FOUT] {e}")
        sys.exit(1)
