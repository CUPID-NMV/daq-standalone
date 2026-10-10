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
    {"mv_per_offset": 0.436, "frequenza": "750Ms", "larghezza_ns": 189.0,
     "polarita": "positiva", "misura": "calibrazione_sipm_larghi_20261009.json",
     "nota": "SiPM; impulsi positivi da 189.34 mV"},
]

# Quanto puo' distare in larghezza un punto perche' lo si consideri
# applicabile. Due e' gia' generoso: fra 1.8 e 96 ns, cioe' un fattore 53, il
# fattore cambia di otto, quindi dentro un fattore due ci si aspetta una
# decina di per cento. Oltre, si preferisce non rispondere.
FATTORE_MAX = 2.0


def scegli(tag_frequenza=None, larghezza_ns=None):
    """(valore, nota) per queste condizioni, oppure (None, perche' no).

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
            "%.3f mV/offset, %s: %s ns %s pulses%s"
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
        "%.3f mV/offset, measured on %s ns %s pulses — this run does not "
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
