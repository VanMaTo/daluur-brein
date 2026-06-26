# ============================================================
#  DALUUR — TOESTELLEN
#  Dit is de ENIGE plek waar je je toestellen beheert.
#
#    Toevoegen      -> kopieer een blok hieronder en pas het aan.
#    Weghalen       -> zet  "actief": False   (of verwijder het hele blok).
#    Volgorde       -> pas  "volgorde"  aan (kleiner = eerst bij AANzetten;
#                      bij UITzetten gaat het automatisch omgekeerd).
#
#  brein.py hoef je hiervoor NOOIT aan te raken.
#
#  Velden per toestel:
#    naam           : vrije naam (verschijnt in de logs)
#    id             : het Tuya device-id
#    actief         : True = meedoen, False = overslaan
#    volgorde       : cascade-volgorde bij aanzetten (1, 2, 3, ...)
#    schakelcode    : None = automatisch herkennen; of vul handmatig in
#                     (bv. "switch" of "switch_1") als de auto-detectie mist
#    meet_verbruik  : True als dit toestel stroom/energie rapporteert
#                     (wordt later gebruikt voor de kWh-logging)
# ============================================================

TOESTELLEN = [

    {
        "naam": "filterpomp",
        "id": "bff78a994663678c5aki4k",
        "actief": True,
        "volgorde": 1,
        "schakelcode": None,
        "meet_verbruik": True,
    },

    {
        "naam": "zoutinstallatie",
        "id": "bf3c1ca3f6090bc3d5jzbw",
        "actief": True,
        "volgorde": 2,
        "schakelcode": None,
        "meet_verbruik": False,
    },

    {
        "naam": "warmtepomp",
        "id": "bff1ab1d1427ccdd9ag0z4",
        "actief": True,
        "volgorde": 3,
        "schakelcode": "switch",      # bevestigd uit de status
        "meet_verbruik": False,
    },

    # ---------------------------------------------------------
    #  VOORBEELD — nieuwe wifi-switch.
    #  Verwijder de # aan het begin van elke regel om hem te activeren,
    #  en plak het juiste device-id.
    # ---------------------------------------------------------
    # {
    #     "naam": "wifi_switch_terras",
    #     "id": "PLAK_HIER_HET_DEVICE_ID",
    #     "actief": True,
    #     "volgorde": 4,
    #     "schakelcode": None,
    #     "meet_verbruik": False,
    # },

]
