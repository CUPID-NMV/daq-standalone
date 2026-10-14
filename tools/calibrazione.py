#!/usr/bin/env python3
"""
I punti di calibrazione offset -> mV del self-trigger del V1742, e la regola
per scegliere quale si applica.

Non e' una tabella indicizzata sulla frequenza di campionamento. Quella era
la forma di prima, ed era una chiave insufficiente: quello che decide il
fattore e' la LARGHEZZA dell'impulso -- fra 1.8 e 189 ns cambia di otto
volte -- mentre la frequenza vale il 25%. Finche' a 750 MS/s stavano i SiPM
e a 1 GS/s i PMT la distinzione non si vedeva; il giorno che si invertono,
una tabella sulla sola frequenza sbaglia di sei volte senza dirlo.

Qui ogni punto porta le condizioni in cui e' stato misurato e il file della
misura, e la scelta si fa sulla larghezza quando la si conosce.
"""

# Ogni punto: mV per unita' di offset, con le condizioni in cui vale.
# "frequenza" None = non registrata nella misura.
PUNTI = [
    {"mv_per_offset": 3.5,   "frequenza": "2.5Gs", "larghezza_ns": 1.8,
     "polarita": "negativa", "misura": "calibrazione_1p6ns_20260925.json",
     "nota": "PMT; misurato a 1.6 ns (3.93) e riportato a 1.80 ns"},
    {"mv_per_offset": 2.7,   "frequenza": "1Gs",   "larghezza_ns": 1.8,
     "polarita": "negativa", "misura": "calibrazione_1p6ns_20260925.json",
     "nota": "PMT; misurato a 1.6 ns (3.13) e riportato a 1.80 ns"},
    {"mv_per_offset": 0.462, "frequenza": None,    "larghezza_ns": 96.0,
     "polarita": "negativa", "misura": "calibrazione_pulser_20260923.json",
     "nota": "la misura non registra la frequenza di campionamento"},
    {"mv_per_offset": 0.42,  "frequenza": "750Ms", "larghezza_ns": 46.7,
     "polarita": "positiva", "misura": "calib_selftrigger_20261013_104413.json",
     "nota": "SiPM, impulsi al buio; misurato dal digitizer stesso su 15 punti "
             "di scan, due stimatori indipendenti a 1 sigma"},
    {"mv_per_offset": 0.436, "frequenza": "750Ms", "larghezza_ns": 189.0,
     "polarita": "positiva", "misura": "calibrazione_sipm_larghi_20261009.json",
     "nota": "SiPM; impulsi positivi da 189.34 mV"},
]

# Il rapporto fra le ampiezze che i due strumenti vedono ai loro ingressi.
# NON e' una proprieta' delle schede: e' una proprieta' dei CAVI -- sdoppiatori,
# attenuatori, l'amplificatore -- e cambia il giorno che qualcuno sposta
# qualcosa al banco. Per questo porta la data e come e' stato ottenuto, e per
# questo non va mai mescolato con i PUNTI qui sopra, che invece descrivono la
# scheda e restano veri.
#
# Serve per una cosa sola, ma importante: uno scan del V812 ha l'asse gia' in
# millivolt ALL'INGRESSO DEL CFD. Per sovrapporgli uno scan del self-trigger,
# gli offset vanno convertiti con la scala riferita allo stesso punto, se no si
# confrontano due assi che si chiamano "mV" e non lo sono nello stesso posto.
# Il rapporto fra le ampiezze ai due ingressi NON E' MISURATO, e qui sta
# scritto perche' non lo e': tre determinazioni dagli stessi dati danno numeri
# incompatibili fra loro.
#
#   0.483   sovrapponendo lo scan del CFD delle 00:09 a quello del self-trigger
#   0.585   sovrapponendo quello delle 09:39, 15 minuti prima del self-trigger
#   0.85    dal BORDO BASSO delle ampiezze registrate durante gli scan del CFD
#           (0.881 alle 00:09, 0.822 alle 09:39), che non usa i rate
#
# Il motivo per cui la sovrapposizione non vale: portando i due scan sulla
# stessa grandezza -- il rate sopra una data ampiezza misurata all'ingresso del
# digitizer -- i due strumenti NON danno lo stesso rate. Il rapporto va da 1.1
# a 2.9 e ha un andamento a U, alto ai due estremi e basso in mezzo. Due curve
# di forma diversa si possono sempre sovrapporre scegliendo la scala giusta in
# un tratto, e il numero che ne esce non e' i millivolt di nessun punto della
# catena.
#
# Perche' le due curve abbiano forma diversa NON si sa. Candidati non
# verificati: il tempo morto della DAQ, che agli alti rate taglia gli scan del
# CFD (una run per punto) diversamente dallo scan del self-trigger (una run
# sola); un canale del CFD che non sta davvero nell'OR; un comportamento del
# constant fraction che non e' una soglia in ampiezza.
#
# Finche' non e' misurato al banco -- un impulso, l'oscilloscopio prima su un
# ingresso e poi sull'altro, dieci minuti -- la pagina non propone nessun
# valore riferito al CFD. Chi vuole provarne uno lo scrive a mano, e il grafico
# lo marca ASSUMED, che e' esattamente quello che e'.
RAPPORTO_CATENE = None
# Quanto puo' distare in larghezza un punto perche' lo si consideri
# applicabile. Due e' gia' generoso: fra 1.8 e 96 ns, cioe' un fattore 53, il
# fattore cambia di otto, quindi dentro un fattore due ci si aspetta una
# decina di per cento. Oltre, si preferisce non rispondere.
FATTORE_MAX = 2.0


def al_cfd(valore):
    """Il fattore riferito all'ingresso del CFD, se mai si sapra' quanto vale.

    Oggi torna None: vedi RAPPORTO_CATENE. Tornare un numero qualunque
    sarebbe peggio che non tornare niente, perche' finirebbe su un asse
    scritto "mV".
    """
    return None if (valore is None or RAPPORTO_CATENE is None) else \
        round(valore * RAPPORTO_CATENE["g"], 4)


def nota_cfd(valore):
    """La nota da mostrare quando nella selezione c'e' anche uno scan del CFD."""
    if RAPPORTO_CATENE is None:
        return ("a V812 scan is in the selection, so the common axis is mV at "
                "the CFD input — but how much of the signal the CFD sees "
                "compared with the digitizer is NOT measured: three ways of "
                "getting it from these data give 0.48, 0.59 and 0.85, and the "
                "two instruments do not even give the same rate above the same "
                "amplitude. Overlaying the curves by eye hides this. Type a "
                "value if you want one, and the plot will label it ASSUMED.")
    return ("%.3f mV/offset at the CFD input: %.3f at the digitizer × %.3f"
            % (al_cfd(valore), valore, RAPPORTO_CATENE["g"]))


def scegli(tag_frequenza=None, larghezza_ns=None):
    """(valore, nota) per queste condizioni, oppure (None, perche' no).

    Il valore e' riferito all'INGRESSO DEL DIGITIZER, che e' dove il
    comparatore del self-trigger guarda. Per riferirlo all'ingresso del CFD,
    cioe' per confrontarlo con uno scan del V812, c'e' al_cfd().

    La nota e' in inglese perche' finisce nella pagina, che si mostra anche
    fuori dal gruppo.
    """
    if larghezza_ns:
        vicini = [p for p in PUNTI
                  if 1.0 / FATTORE_MAX <= p["larghezza_ns"] / larghezza_ns <= FATTORE_MAX]
        if not vicini:
            vicino = min(PUNTI, key=lambda p: abs(
                _log(p["larghezza_ns"]) - _log(larghezza_ns)))
            return None, ("no calibration within a factor %g of the %.0f ns pulses "
                          "in this run (nearest: %s ns). Type a value, and the "
                          "plot will label it ASSUMED."
                          % (FATTORE_MAX, larghezza_ns, _ns(vicino["larghezza_ns"])))
        # Fra i punti compatibili in larghezza vince quello della stessa
        # frequenza di campionamento: e' la correzione piu' piccola, ma e'
        # misurata.
        stessa = [p for p in vicini if p["frequenza"] == tag_frequenza]
        p = (stessa or vicini)[0]
        come = "measured" if stessa else "nearest in pulse width"
        return p["mv_per_offset"], (
            "%.3f mV/offset at the digitizer input, %s: %s ns %s pulses%s"
            % (p["mv_per_offset"], come, _ns(p["larghezza_ns"]), _verso(p),
               "" if stessa else " at %s" % _freq(p)))

    # Senza la larghezza resta la frequenza, che e' una chiave debole: si
    # risponde, ma dicendo su che impulso quel numero e' stato misurato, cosi'
    # chi guarda vede subito se non c'entra niente con i suoi.
    per_freq = [p for p in PUNTI if p["frequenza"] == tag_frequenza]
    if not per_freq:
        return None, ("no calibration for %s. Type a value, and the plot will "
                      "label it ASSUMED." % (tag_frequenza or "this sampling rate"))
    p = per_freq[0]
    return p["mv_per_offset"], (
        "%.3f mV/offset at the digitizer input, measured on %s ns %s pulses — "
        "this run does not "
        "record its pulse width, so check it is the same kind of signal"
        % (p["mv_per_offset"], _ns(p["larghezza_ns"]), _verso(p)))


def _ns(x):
    """Una larghezza come si legge: 1.8 ns, non "2 ns"."""
    return ("%.1f" if x < 10 else "%.0f") % x


def _verso(p):
    return {"positiva": "positive", "negativa": "negative"}.get(p["polarita"], "")


def _freq(p):
    return p["frequenza"] or "an unrecorded sampling rate"


def _log(x):
    import math
    return math.log(max(x, 1e-9))
