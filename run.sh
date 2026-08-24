#!/bin/bash
# ============================================================
#  DALUUR — STARTER voor het brein
#
#  Drie beveiligingen, alle drie geleerd van de storing van 24/08/2026
#  (vijf vastgelopen processen, zwembad een halve dag uit):
#
#    1. flock    -> nooit twee rondes tegelijk. Draait de vorige nog,
#                   dan slaat deze over in plaats van erbovenop te stapelen.
#    2. timeout  -> een ronde duurt nooit langer dan 10 minuten. Daarna
#                   wordt hij afgebroken, hoe vast hij ook zit.
#    3. melding  -> wordt hij afgebroken, dan gaat dat door de waakhond
#                   en krijg je een Telegram-bericht. Geen stille storing.
# ============================================================

MAP=/home/markvanherf/daluur-brein
LOG=$MAP/brein.log

cd "$MAP" || exit 1
set -a
source .env
set +a

# --- 1. slot ---
exec 9>/var/tmp/daluur_brein.lock
if ! flock -n 9; then
    echo "[$(date '+%Y-%m-%d %H:%M')] vorige ronde draait nog — deze overgeslagen" >> "$LOG"
    exit 0
fi

# --- 2. de ronde zelf, met tijdslimiet ---
timeout --signal=TERM --kill-after=30 600 /usr/bin/python3 brein.py 2>&1 \
    | tee -a "$LOG" \
    | /usr/bin/python3 waakhond.py > /dev/null

# --- 3. afgebroken? dan alarm slaan ---
if [ "${PIPESTATUS[0]}" -eq 124 ]; then
    echo "  brein-ronde mislukt: afgebroken na 10 minuten (vastgelopen verbinding?)" \
        | tee -a "$LOG" \
        | /usr/bin/python3 waakhond.py > /dev/null
fi
