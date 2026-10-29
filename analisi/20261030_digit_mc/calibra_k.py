"""Scala ADC*ns per fotoelettrone (K) del MC digitalizzato, dai muoni.

    python3 analisi/20261030_digit_mc/calibra_k.py MUONI_DIGIT.npz

Confronta la mediana della carica per PMT dei muoni MC digitalizzati
(traccia in acqua > 0, entrambi i PMT sopra 3 sigma) con quella dei muoni
di Frascati (tre SiPM sopra soglia, cancello della 0011 allineato
sull'impulso) e scrive in measurements/mc_digit_K.json il K che le fa
coincidere. La carica digitalizzata e' lineare in K salvo il rumore, quindi
basta un'iterazione; una seconda passata di digit_mc.py lo verifica.
K contiene il guadagno del PMT e quello della scheda di amplificazione,
che non e' noto: va sostituito dal fotoelettrone singolo misurato.
"""
# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))

import json
import sys
import numpy as np

MIS = _os.path.join(ROOT, "measurements")
m = np.load(sys.argv[1])
S = np.load(_os.path.join(MIS, "libreria_digit_0013.npz"))["sigma_q"]
fra = np.load(_os.path.join(MIS, "carica_frascati_0005_gateLNGS_ped78-228.npz"))
sel = np.load(_os.path.join(MIS, "frascati_0005_sel_3sipm_500k.npy"))
lnf = float(0.5 * (np.median(fra["q_caen_ch24"][sel]) + np.median(fra["q_caen_ch26"][sel])))
q = m["q"]
ok = (m["trackl"] > 0) & (q[:, 0] > 3 * S[0]) & (q[:, 1] > 3 * S[1])
mc = float(np.median(q[ok].ravel()))
k_old = float(m["k"])
k_new = k_old * lnf / mc
print("muoni MC usati %d; mediana carica MC %.0f con K = %.0f; LNF %.0f -> K = %.0f ADC*ns/PE"
      % (ok.sum(), mc, k_old, lnf, k_new))
json.dump({"K_adcns_per_pe": k_new,
           "da": "mediana della carica per PMT, muoni MC digitalizzati contro muoni LNF "
                 "(Frascati 0005, 3 SiPM, cancello LNGS)",
           "mediana_lnf": lnf, "mediana_mc": mc, "k_prima": k_old},
          open(_os.path.join(MIS, "mc_digit_K.json"), "w"), indent=1)
print("scritto measurements/mc_digit_K.json")
