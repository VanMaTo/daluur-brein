#!/usr/bin/env python3
# ============================================================
#  DALUUR — PRIJZEN-OPHALER (avond)
#  Haalt de prijzen voor MORGEN op en schrijft ze weg zodra ze
#  bekend zijn (PVPC verschijnt rond 20:15 Spaanse tijd).
#
#  Bedoeld om vanaf ~20:15 ELKE MINUUT te draaien:
#    - staat morgen al compleet in de database  -> meteen stoppen (goedkoop)
#    - nog niet beschikbaar bij REE             -> stil stoppen, volgende
#                                                  minuut opnieuw
#    - net beschikbaar                          -> ophalen + wegschrijven
#
#  Het grote brein (brein.py) blijft ongemoeid en draait gewoon elk uur.
# ============================================================

import datetime
import requests

# hergebruik exact dezelfde logica als het brein
from brein import TZ, SUPABASE_URL, _sb_headers, fetch_prices, schrijf_prijzen


def prijzen_compleet(datum):
    """True als er al minstens 23 uurprijzen voor 'datum' in Supabase staan."""
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/prijzen?datum=eq.{datum}&select=uur",
            headers=_sb_headers(), timeout=15)
        r.raise_for_status()
        return len(r.json()) >= 23
    except Exception as e:
        print(f"  check bestaande prijzen mislukt: {e}")
        return False


def main():
    now = datetime.datetime.now(TZ)
    morgen = (now.date() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    stamp = now.strftime("%Y-%m-%d %H:%M")

    # 1) al binnen? -> niets te doen, meteen klaar
    if prijzen_compleet(morgen):
        print(f"[PRIJZEN] {stamp} — morgen ({morgen}) staat al compleet. Klaar.")
        return

    # 2) proberen op te halen (fetch_prices pakt enkel echte PVPC, geen spot)
    try:
        prijzen = fetch_prices(morgen)
    except Exception as e:
        print(f"[PRIJZEN] {stamp} — morgen ({morgen}) nog niet beschikbaar: {e}")
        return

    # 3) wegschrijven en controleren
    schrijf_prijzen(morgen, prijzen)
    if prijzen_compleet(morgen):
        print(f"[PRIJZEN] {stamp} — GELUKT: {len(prijzen)} uurprijzen voor morgen "
              f"({morgen}) weggeschreven.")
    else:
        print(f"[PRIJZEN] {stamp} — weggeschreven maar nog niet compleet; "
              f"volgende minuut opnieuw.")


if __name__ == "__main__":
    main()
