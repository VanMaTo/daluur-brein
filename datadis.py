#!/usr/bin/env python3
# ============================================================
#  DALUUR — DATADIS-OPHALER (ochtend)
#  Haalt het officiële uurverbruik van de digitale meter op bij
#  Datadis (e-distribución) en schrijft het in Supabase-tabel
#  meter_uur. Daar rekenen kost_uur en kost_dag mee.
#
#  Draait een keer per dag via cron (run_datadis.sh).
#    - vraagt de vorige en de huidige maand op (Datadis kent enkel
#      hele maanden als periode, geen losse dagen)
#    - overschrijft bestaande uren: een dag die Datadis later nog
#      aanvult, wordt zo vanzelf compleet
#    - Datadis "01:00" = verbruik 00-01u = uur 0 in meter_uur
#    - wintertijd (laatste zondag oktober): het dubbele uur wordt
#      opgeteld, net als bij de handmatige CSV
#    - Datadis laat dezelfde maand maar een keer per dag opvragen
#      (anders fout 429): dan wordt die maand overgeslagen, zonder
#      alarm; de andere maand wordt wel weggeschreven
#    - Telegram-melding als het ophalen mislukt of de data al
#      dagen niet meer aangroeit
#
#  Handmatig een maand (bij)laden:  python3 datadis.py 2026/08
# ============================================================

import os
import sys
import datetime

import requests

# hergebruik dezelfde instellingen als het brein
from brein import TZ, SUPABASE_URL, stuur_telegram

DATADIS_USER = os.environ["DATADIS_USER"].strip()
DATADIS_PASS = os.environ["DATADIS_PASS"]
DATADIS_CUPS = os.environ["DATADIS_CUPS"].strip()
DISTRIBUTEUR = "2"      # e-distribución
POINT_TYPE   = "5"      # gewone huisaansluiting
# Schrijven in meter_uur mag niet met de publieke anon-sleutel: daarom de
# geheime service-sleutel van Supabase (enkel in .env op de Pi).
SUPABASE_SERVICE = os.environ["SUPABASE_SERVICE"].strip()

LOGIN_URL = "https://datadis.es/nikola-auth/tokens/login"
VERBRUIK_URL = "https://datadis.es/api-private/api/get-consumption-data"
DATADIS_TIMEOUT = 180   # Datadis is soms traag
BRON = "datadis api"
MAX_ACHTERSTAND = 4     # dagen zonder nieuwe data voor we alarm slaan


def _service_headers(extra=None):
    # Nieuwe Supabase-sleutels (sb_secret_...) gaan enkel in 'apikey';
    # een oude service_role-sleutel (JWT) ook in 'Authorization'.
    h = {"apikey": SUPABASE_SERVICE, "Content-Type": "application/json"}
    if not SUPABASE_SERVICE.startswith("sb_"):
        h["Authorization"] = f"Bearer {SUPABASE_SERVICE}"
    if extra:
        h.update(extra)
    return h


def datadis_login():
    r = requests.post(LOGIN_URL, data={"username": DATADIS_USER,
                                       "password": DATADIS_PASS},
                      timeout=60)
    r.raise_for_status()
    token = r.text.strip()
    if not token.startswith("eyJ"):
        raise RuntimeError(f"login gaf geen token terug: {token[:80]}")
    return token


def haal_maand(token, maand):
    """maand als 'YYYY/MM' -> lijst van Datadis-regels voor die maand."""
    r = requests.get(VERBRUIK_URL, headers={"Authorization": f"Bearer {token}"},
                     params={"cups": DATADIS_CUPS, "distributorCode": DISTRIBUTEUR,
                             "startDate": maand, "endDate": maand,
                             "measurementType": "0", "pointType": POINT_TYPE},
                     timeout=DATADIS_TIMEOUT)
    r.raise_for_status()
    return r.json() or []


def naar_rijen(items):
    """Datadis-regels -> rijen voor meter_uur (datum, uur, kwh, bron).
    Twee regels voor hetzelfde uur (wintertijd) worden opgeteld."""
    som = {}
    for it in items:
        kwh = it.get("consumptionKWh")
        if kwh is None:
            continue
        datum = it["date"].replace("/", "-")        # 2026/10/01 -> 2026-10-01
        uur = int(it["time"].split(":")[0]) - 1     # 01:00 -> 0 ... 24:00 -> 23
        if not 0 <= uur <= 23:
            continue
        som[(datum, uur)] = som.get((datum, uur), 0.0) + float(kwh)
    return [{"datum": d, "uur": u, "kwh": round(k, 3), "bron": BRON}
            for (d, u), k in sorted(som.items())]


def schrijf_meter(rijen):
    r = requests.post(f"{SUPABASE_URL}/rest/v1/meter_uur?on_conflict=datum,uur",
                      headers=_service_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
                      json=rijen, timeout=60)
    r.raise_for_status()


def main():
    now = datetime.datetime.now(TZ)
    stamp = now.strftime("%Y-%m-%d %H:%M")
    if len(sys.argv) > 1:
        maanden = sys.argv[1:]                      # handmatig, bv. 2026/08
    else:
        eerste = now.date().replace(day=1)
        vorige = (eerste - datetime.timedelta(days=1)).replace(day=1)
        maanden = [vorige.strftime("%Y/%m"), eerste.strftime("%Y/%m")]

    try:
        token = datadis_login()
        items = []
        beperkt = []                                # maanden geweigerd met 429
        for m in maanden:
            try:
                items += haal_maand(token, m)
            except requests.HTTPError as e:
                if e.response is None or e.response.status_code != 429:
                    raise
                beperkt.append(m)
                print(f"[DATADIS] {stamp} — {m} vandaag al opgevraagd (429), overgeslagen")
        rijen = naar_rijen(items)
        if rijen:
            schrijf_meter(rijen)
    except Exception as e:
        print(f"[DATADIS] {stamp} — ophalen mislukt: {e}")
        stuur_telegram(f"Datadis: uurverbruik ophalen mislukt ({stamp}):\n{e}")
        return

    if not rijen:
        if len(beperkt) == len(maanden):
            return                                  # alles al opgevraagd vandaag
        print(f"[DATADIS] {stamp} — geen data ontvangen voor {', '.join(maanden)}")
        stuur_telegram(f"Datadis: geen data ontvangen voor {', '.join(maanden)} ({stamp}).")
        return

    laatste = rijen[-1]["datum"]
    achter = (now.date() - datetime.date.fromisoformat(laatste)).days
    print(f"[DATADIS] {stamp} — {len(rijen)} uren weggeschreven "
          f"({rijen[0]['datum']} t/m {laatste}).")
    if len(sys.argv) == 1 and not beperkt and achter > MAX_ACHTERSTAND:
        stuur_telegram(f"Datadis: laatste meterdata is van {laatste}, "
                       f"{achter} dagen geleden. Kijk op datadis.es.")


if __name__ == "__main__":
    main()
