#!/usr/bin/env python3
# ============================================================
#  DALUUR — BREIN
#  Draait elk uur via GitHub Actions.
#  1. leest de planning uit Supabase (of berekent de N goedkoopste)
#  2. beslist of dit Spaanse uur AAN of UIT moet
#  3. voert de Tuya-cascade uit (met pauze tussen de toestellen)
#  4. drukt de toestand van de toestellen af (om verbruik te kalibreren)
# ============================================================

import os
import sys
import time
import datetime
from zoneinfo import ZoneInfo

import requests
from tinytuya import Cloud

# ---------- configuratie (niet geheim) ----------
TZ = ZoneInfo("Europe/Madrid")          # planning is in Spaanse lokale tijd
DEFAULT_N = 4                            # niets gepland -> N goedkoopste uren
PAUZE = 60                              # seconden tussen de toestellen
SWITCH_CODE = "switch_1"

DEVICES = {                             # naam -> Tuya device-id
    "filterpomp":      "bff78a994663678c5aki4k",
    "zoutinstallatie": "bf3c1ca3f6090bc3d5jzbw",
    "warmtepomp":      "bff1ab1d1427ccdd9ag0z4",
}
ON_ORDER  = ["filterpomp", "zoutinstallatie", "warmtepomp"]   # aanzetten: circulatie eerst
OFF_ORDER = ["warmtepomp", "zoutinstallatie", "filterpomp"]   # uitzetten: warmtepomp eerst

# ---------- geheimen / omgeving (uit GitHub) ----------
SUPABASE_URL  = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_ANON = os.environ["SUPABASE_ANON"]
TUYA_REGION   = os.environ.get("TUYA_REGION", "eu")
TUYA_ID       = os.environ["TUYA_CLIENT_ID"]
TUYA_SECRET   = os.environ["TUYA_SECRET"]


# ---------- Supabase ----------
def _sb_headers(extra=None):
    h = {
        "apikey": SUPABASE_ANON,
        "Authorization": f"Bearer {SUPABASE_ANON}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def get_plan(datum):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/planning?datum=eq.{datum}&select=n,uren",
        headers=_sb_headers(), timeout=15,
    )
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else None


def upsert_plan(datum, n, uren, handmatig=False):
    requests.post(
        f"{SUPABASE_URL}/rest/v1/planning",
        headers=_sb_headers({"Prefer": "resolution=merge-duplicates"}),
        json={
            "datum": datum, "n": n, "uren": uren, "handmatig": handmatig,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        timeout=15,
    )


# ---------- prijzen (alleen nodig als er geen planning is) ----------
def fetch_prices(datum):
    """datum = 'YYYY-MM-DD' -> {uur: prijs eur/kWh}"""
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


def get_switch(c, dev_id):
    """Huidige stand van switch_1, of None als onbekend."""
    try:
        st = c.getstatus(dev_id)
        for item in st.get("result", []):
            if item.get("code") == SWITCH_CODE:
                return bool(item.get("value"))
    except Exception as e:
        print(f"    status lezen mislukt: {e}")
    return None


def set_switch(c, dev_id, value):
    return c.sendcommand(dev_id, {"commands": [{"code": SWITCH_CODE, "value": bool(value)}]})


def cascade(c, value):
    order = ON_ORDER if value else OFF_ORDER
    for i, naam in enumerate(order):
        print(f"    -> {naam} = {value}")
        res = set_switch(c, DEVICES[naam], value)
        if isinstance(res, dict) and res.get("success") is False:
            print(f"       LET OP, Tuya gaf terug: {res}")
        if i < len(order) - 1:
            time.sleep(PAUZE)


def dump_status(c):
    """Drukt alle datapunten af zodat we het echte verbruik kunnen kalibreren."""
    print("  [TOESTAND] datapunten per toestel:")
    for naam, dev in DEVICES.items():
        try:
            st = c.getstatus(dev)
            print(f"    {naam}: {st.get('result', st)}")
        except Exception as e:
            print(f"    {naam}: fout {e}")


# ---------- hoofdlogica ----------
def main():
    now = datetime.datetime.now(TZ)
    datum = now.strftime("%Y-%m-%d")
    uur = now.hour
    print(f"[DALUUR-BREIN] {now:%Y-%m-%d %H:%M} (Madrid) — uur {uur}")

    # 1) planning bepalen
    uren, bron = None, None
    try:
        plan = get_plan(datum)
    except Exception as e:
        plan = None
        print(f"  planning ophalen mislukt: {e}")

    if plan and isinstance(plan.get("uren"), list):
        uren = [int(x) for x in plan["uren"]]
        bron = "opgeslagen planning"
    else:
        # geen planning -> N goedkoopste berekenen
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
            print("  FAIL-SAFE: niets bekend -> niets schakelen, gestopt.")
            return

    moet_aan = uur in uren
    print(f"  bron: {bron}")
    print(f"  actieve uren: {uren}")
    print(f"  dit uur ({uur:02d}) moet {'AAN' if moet_aan else 'UIT'}")

    # 2) Tuya
    c = cloud()
    dump_status(c)

    huidig = get_switch(c, DEVICES["filterpomp"])
    print(f"  huidige stand (filterpomp): {huidig}")

    if huidig is not None and huidig == moet_aan:
        print("  stand klopt al — geen cascade nodig.")
    else:
        print(f"  cascade naar {'AAN' if moet_aan else 'UIT'} ...")
        cascade(c, moet_aan)
        print("  cascade klaar.")

    # 3) verbruik loggen
    # Wordt ingevuld zodra we uit de [TOESTAND]-afdruk hierboven weten
    # welke datapunten jouw toestellen voor energie/vermogen gebruiken.


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[FOUT] {e}")
        sys.exit(1)
