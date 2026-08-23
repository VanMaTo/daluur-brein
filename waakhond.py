#!/usr/bin/env python3
# ============================================================
#  DALUUR — WAAKHOND
#  Leest mee over de schouder van brein.py en slaat alarm via
#  Telegram als er iets misloopt.
#
#  Waarom een apart script?
#    brein.py blijft ongemoeid. De waakhond leest gewoon de
#    uitvoer die brein.py toch al produceert. Werkt het niet
#    naar wens, dan haal je hem uit run.sh en alles draait
#    weer zoals vroeger.
#
#  Twee taken:
#    1. ALARM      -> ziet hij een foutwoord in de uitvoer, dan
#                     stuurt hij je een Telegram-bericht.
#                     Hoogstens een keer per 6 uur, zodat je
#                     telefoon niet elk uur afgaat bij een
#                     storing die dagen duurt.
#    2. LEVENSTEKEN-> een keer per week een "alles in orde".
#                     Blijft dat uit, dan ligt de Pi zelf plat.
#
#  Gebruik (in run.sh):
#    python3 brein.py 2>&1 | tee -a brein.log | python3 waakhond.py
# ============================================================

import os
import sys
import time
import datetime

import requests

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# Waar we onthouden wanneer we voor het laatst iets stuurden.
# /var/tmp overleeft een herstart niet als de kaart op alleen-lezen
# staat — dat is prima: je krijgt dan hoogstens een bericht te veel.
STAAT_MAP     = "/var/tmp"
ALARM_STEMPEL = os.path.join(STAAT_MAP, "daluur_laatste_alarm")
LEEF_STEMPEL  = os.path.join(STAAT_MAP, "daluur_laatste_levensteken")

ALARM_PAUZE = 6 * 3600        # niet vaker dan om de 6 uur alarm slaan
LEEF_PAUZE  = 7 * 24 * 3600   # een levensteken per week

# Woorden die op een storing wijzen. Kleine letters — we vergelijken
# op een verkleinde versie van de regel.
FOUTWOORDEN = [
    "props-fout",
    "expired",
    "mislukt",
    "traceback",
    "error",
    "no permissions",
    "unauthorized",
    "timed out",
]

# Regels die op een foutwoord lijken maar onschuldig zijn.
UITZONDERINGEN = [
    "telegram-bericht mislukt",   # anders melden we een mislukte melding
    "nog niet beschikbaar",       # prijzen van morgen zijn er simpelweg nog niet
    "staat al compleet",          # prijzen al binnen, niets aan de hand
]


def stuur_telegram(tekst):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": tekst},
            timeout=10,
        )
        return r.ok
    except Exception:
        return False


def lang_genoeg_geleden(pad, pauze):
    """True als het bestand niet bestaat of ouder is dan 'pauze' seconden."""
    try:
        return (time.time() - os.path.getmtime(pad)) > pauze
    except OSError:
        return True


def stempel(pad):
    try:
        os.makedirs(STAAT_MAP, exist_ok=True)
        with open(pad, "w") as f:
            f.write(datetime.datetime.now().isoformat())
    except OSError:
        pass


def is_verdacht(regel):
    laag = regel.lower()
    if any(u in laag for u in UITZONDERINGEN):
        return False
    return any(w in laag for w in FOUTWOORDEN)


def main():
    regels = []
    verdacht = []

    # Alles wat brein.py uitspuwt gaat hier doorheen en meteen weer
    # naar buiten, zodat je bij handmatig draaien niets mist.
    for regel in sys.stdin:
        regel = regel.rstrip("\n")
        print(regel)
        regels.append(regel)
        if is_verdacht(regel):
            verdacht.append(regel.strip())

    nu = datetime.datetime.now().strftime("%d/%m %H:%M")

    if verdacht:
        if lang_genoeg_geleden(ALARM_STEMPEL, ALARM_PAUZE):
            # hoogstens de eerste vijf, anders wordt het bericht onleesbaar
            kern = "\n".join(f"• {r}" for r in verdacht[:5])
            meer = f"\n… en nog {len(verdacht) - 5} andere regels." if len(verdacht) > 5 else ""
            bericht = (
                f"⚠️ Daluur-brein — storing ({nu})\n\n"
                f"{kern}{meer}\n\n"
                f"Je zwembad en warmtepomp volgen mogelijk geen schema meer.\n\n"
                f"Vaakst voorkomende oorzaak: het Tuya-abonnement is verlopen. "
                f"Kijk op iot.tuya.com bij IoT Core → My Subscriptions."
            )
            if stuur_telegram(bericht):
                stempel(ALARM_STEMPEL)
        # bij een storing geen levensteken sturen — dat zou verwarrend zijn
        return

    # Geen storing: af en toe laten weten dat alles nog draait.
    if lang_genoeg_geleden(LEEF_STEMPEL, LEEF_PAUZE):
        samenvatting = "\n".join(regels[:3]) if regels else "(geen uitvoer)"
        if stuur_telegram(
            f"✅ Daluur-brein draait normaal ({nu})\n\n{samenvatting}\n\n"
            f"Dit bericht komt een keer per week. Blijft het uit, "
            f"dan ligt de Pi in Arenas stil."
        ):
            stempel(LEEF_STEMPEL)


if __name__ == "__main__":
    main()
