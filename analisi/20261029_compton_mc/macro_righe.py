"""Scrive gun_gammas_background_righe.mac: la stessa sorgente di
gun_gammas_background.mac (superficie attorno al prototipo, legge del
coseno verso l'interno), ma con lo spettro a RIGHE dei colleghi CUPID
(histo_environmental_gamma_CUPID_python_extract_true_spectrum.txt) invece
dello spettro misurato col NaI.

    python3 analisi/20261029_compton_mc/macro_righe.py PROGETTO_MC

Perche': lo spettro della macro attuale e' quello DEPOSITATO in un NaI 3"x3"
(Bellini et al. 2013, Fig. 3), che contiene il continuo Compton del cristallo
e sottopesa le righe alte. Le righe sono lo spettro emesso: sopra 0.42 MeV,
dove un gamma puo' fare luce Cherenkov in acqua, ci sono il 30% dei gamma
contro il 13%; per il Tl-208 il 0.9% contro il 0.1%. Mancando il continuo
dei gamma diffusi nella roccia, le righe con lo stesso flusso totale danno il
limite SUPERIORE del fondo Compton, la macro attuale quello inferiore.

Ogni riga diventa un bin largo 2*DELTA attorno alla sua energia, in un
istogramma GPS di tipo "User" (/gps/hist/type energy): ogni punto e'
(bordo superiore del bin, peso), e dentro il bin l'energia e' uniforme.
Righe piu' vicine di 2*DELTA si fondono, pesate.
"""
import os
import sys

import numpy as np

PROG = sys.argv[1]
SRC = os.path.join(PROG, "Analysis_script_background",
                   "histo_environmental_gamma_CUPID_python_extract_true_spectrum.txt")
OUT = os.path.join(PROG, "gun_gammas_background_righe.mac")
DELTA = 0.00025          # MeV: mezza larghezza del bin di ogni riga (0.25 keV)

E, W = [], []
for ln in open(SRC):
    p = ln.split()
    try:
        E.append(float(p[0]) / 1000.0)     # keV -> MeV
        W.append(float(p[1]))
    except (ValueError, IndexError):
        pass
o = np.argsort(E)
E, W = np.array(E)[o], np.array(W)[o]

# fonde le righe troppo vicine (media pesata)
fE, fW = [E[0]], [W[0]]
for e, w in zip(E[1:], W[1:]):
    if e - fE[-1] < 2 * DELTA:
        fE[-1] = (fE[-1] * fW[-1] + e * w) / (fW[-1] + w)
        fW[-1] += w
    else:
        fE.append(e)
        fW.append(w)
fE, fW = np.array(fE), np.array(fW) / np.sum(fW)

righe = []
for e, w in zip(fE, fW):
    righe.append("/gps/hist/point %.6f 0" % (e - DELTA))     # bin vuoto fino alla riga
    righe.append("/gps/hist/point %.6f %.6e" % (e + DELTA, w))

testo = """# =======================================================
# SORGENTE: fondo gamma da radioattivita' naturale (LNGS Hall A)
# SPETTRO A RIGHE -- variante di gun_gammas_background.mac
# Da includere DOPO /run/initialize
#
# Generata da daq-standalone/analisi/20261029_compton_mc/macro_righe.py
# a partire da Analysis_script_background/
#   histo_environmental_gamma_CUPID_python_extract_true_spectrum.txt
# (righe di emissione, colleghi CUPID): %d righe, %.3f-%.3f MeV.
#
# Perche' due macro: gun_gammas_background.mac usa lo spettro DEPOSITATO in
# un NaI 3"x3" (Bellini 2013, Fig. 3), che sottopesa le righe alte; questa
# usa le righe emesse, senza il continuo dei gamma diffusi nella roccia.
# Con lo stesso flusso (Hall A, J = 0.28 cm^-2 s^-1 per cm^2 di superficie) la prima da' il limite inferiore
# del fondo Compton, questa quello superiore.
# Frazione dei gamma sopra 0.42 MeV (soglia Cherenkov via Compton):
# %.1f%% qui, ~12.7%% nella macro NaI.
#
# Ogni riga e' un bin di %.1f keV; dentro il bin l'energia e' uniforme.
# =======================================================

/gps/particle gamma

# Sorgente isotropica dalla superficie del parallelepipedo esterno al tank
# (identica a gun_gammas_background.mac: la normalizzazione phi/4 * A vale
# per entrambe)
/gps/pos/type Surface
/gps/pos/shape Para
/gps/pos/centre 0 0 0 mm
/gps/pos/halfx 650 mm
/gps/pos/halfy 650 mm
/gps/pos/halfz 150 mm
/gps/ang/type cos

# --- Spettro a righe (istogramma utente, energie in MeV) ---
/gps/ene/type User
/gps/hist/type energy
%s
""" % (len(E), E.min(), E.max(), 100 * fW[fE > 0.42].sum(), 2000 * DELTA, "\n".join(righe))
open(OUT, "w").write(testo)
print("scritto %s: %d righe (%d dopo la fusione), %.1f%% sopra 0.42 MeV, %.2f%% sopra 2.6 MeV"
      % (OUT, len(E), len(fE), 100 * fW[fE > 0.42].sum(), 100 * fW[fE > 2.6].sum()))
