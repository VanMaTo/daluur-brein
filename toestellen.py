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
#    naam         : vrije naam (verschijnt in de logs)
#    id           : het Tuya device-id
#    actief       : True = meedoen, False = overslaan
#    volgorde     : cascade-volgorde bij aanzetten (1, 2, 3, ...)
#    schakelcode  : de aan/uit-code (None = automatisch herkennen)
#    groep        : welk schema stuurt dit toestel? Toestellen in dezelfde groep
#                   delen één urenschema in de app. Bv. "circulatie" (pomp+zout)
#                   en "wp" (warmtepomp) hebben elk hun eigen planning.
#    hangt_af_van : (optioneel) naam van de groep die dit toestel van stroom
#                   voorziet. Draait die groep dit uur niet, dan wordt het
#                   toestel overgeslagen (stroomloos). Bv. de WP hangt via de
#                   flow-teleruptor aan "circulatie": geen circulatie = geen WP.
#
#  Verbruik meten (optioneel — laat meet_code op None als er niets te meten valt):
#    meet_code    : datapunt met het verbruik
#    meet_soort   : "teller"   = cumulatieve kWh-teller (we nemen het verschil)
#                   "vermogen" = momentaan vermogen in W (we rekenen W x tijd)
#    meet_schaal  : deel de ruwe waarde door dit getal (Tuya geeft hele getallen)
# ============================================================

TOESTELLEN = [

    {
        "naam": "filterpomp",
        "id": "bff78a994663678c5aki4k",
        "actief": True,
        "volgorde": 1,
        "groep": "circulatie",
        "schakelcode": "swtich",      # let op: typefout in de firmware (geen 'switch')
        "meet_code": "power_consumption",
        "meet_soort": "teller",
        "meet_schaal": 100,
    },

    {
        "naam": "zoutinstallatie",
        "id": "bf3c1ca3f6090bc3d5jzbw",
        "actief": True,
        "volgorde": 2,
        "groep": "circulatie",
        "schakelcode": "on_off",
        "meet_code": "p",             # elektrolysevermogen (W)
        "meet_soort": "vermogen",
        "meet_schaal": 100,
    },

    {
        "naam": "warmtepomp",
        "id": "bff1ab1d1427ccdd9ag0z4",
        "actief": True,               # nu apart aangestuurd (eigen schema)
        "volgorde": 3,
        "groep": "wp",
        "hangt_af_van": "circulatie", # krijgt via flow-teleruptor pas stroom bij circulatie
        "schakelcode": "switch",
        "meet_code": None,            # geen verbruiks-datapunt via Tuya
        "meet_soort": None,
        "meet_schaal": 1,
    },

    # ---------------------------------------------------------
    #  VOORBEELD — nieuwe wifi-switch.
    #  Verwijder de # aan het begin van elke regel om hem te activeren.
    # ---------------------------------------------------------
    # {
    #     "naam": "wifi_switch_terras",
    #     "id": "PLAK_HIER_HET_DEVICE_ID",
    #     "actief": True,
    #     "volgorde": 4,
    #     "schakelcode": None,
    #     "meet_code": None,
    #     "meet_soort": None,
    #     "meet_schaal": 1,
    # },

]
