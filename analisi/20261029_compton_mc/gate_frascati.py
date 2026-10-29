"""Muoni di Frascati con il cancello della 0011 riportato sull'impulso.

    python3 analisi/20261029_compton_mc/gate_frascati.py F_FRASCATI F_LNGS_0011 OUT.png

Il cancello della 0011 (240-500 ns) sta a -25/+235 ns dal picco dei PMT
(~264 ns). A Frascati il picco e' a ~341 ns: +78 ns. Lo spostamento si
MISURA sull'impulso e non si calcola dal PostTrigger: con 60% invece di 55%
il trigger andrebbe 67 ns piu' indietro e l'impulso cadrebbe PRIMA, invece
cade dopo -- conta il percorso del trigger (V812 contro self-trigger).

Legge le cariche da measurements/carica_frascati_0005_* (scritte da
tools/carica_padme.py) e le forme d'onda medie dai due file di dati.
"""
# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
_sys.path.insert(0, _os.path.join(ROOT, "tools"))

import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import padme

FF, FL, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
MIS = _os.path.join(ROOT, "measurements")
SHIFT = 78.0
sel = np.load(_os.path.join(MIS, "frascati_0005_sel_3sipm_500k.npy"))
vecchio = np.load(_os.path.join(MIS, "carica_frascati_0005_p0-150_g150-600.npz"))
nuovo = np.load(_os.path.join(MIS, "carica_frascati_0005_gateLNGS_ped78-228.npz"))
MUONI_LNGS = [5827.0, 3633.0, 4602.0]
PE_MU_MC = 4.86        # PE per PMT, mediana dei muoni MC che attraversano (compton_vs_dati.py)


def media(f, chs, n=20000):
    h, c, d, s = padme.leggi(f, chs, n, serve_sic=False)
    t = np.arange(d.shape[2]) * 4 / 3
    y = d.astype(float) - d[:, :, :110].mean(2, keepdims=True)
    return t, y.mean(0)


tF, mF = media(FF, [24, 26])
tL, mL = media(FL, [20, 21])

fig, ax = plt.subplots(1, 3, figsize=(19, 5.8), width_ratios=[1.25, 1, 1])
a = ax[0]
a.plot(tF, mF[0], color="#2a6fdb", lw=1.1, label="Frascati 0005 ch24, mean of all events")
a.plot(tL + SHIFT, mL[0], color="#e8710a", lw=1.1,
       label="LNGS 0011 ch20, mean of all events, shifted by +%.0f ns" % SHIFT)
a.axvspan(150, 600, color="#2a6fdb", alpha=0.07, label="old Frascati gate 150-600 ns")
a.axvspan(240 + SHIFT, 500 + SHIFT, color="#e8710a", alpha=0.15,
          label="LNGS gate 240-500 ns → Frascati %.0f-%.0f ns" % (240 + SHIFT, 500 + SHIFT))
a.set_xlim(0, 1350)
a.set_xlabel("time in the Frascati trace [ns]")
a.set_ylabel("ADC − pedestal [counts] (negative pulses)")
a.set_title("Where the gates sit on the pulse (the two mean waveforms are\nnot to scale: different triggers)",
            fontsize=10, loc="left")
a.legend(fontsize=7.5, frameon=False, loc="lower right")
a.grid(alpha=0.25)

b = np.linspace(-1000, 40000, 165)
for a, ch in ((ax[1], 24), (ax[2], 26)):
    qv, qn = vecchio["q_caen_ch%d" % ch][sel], nuovo["q_caen_ch%d" % ch][sel]
    a.hist(qv, b, histtype="step", color="#2a6fdb", lw=1.3,
           label="gate 150-600, ped 0-150: median %.0f" % np.median(qv))
    a.hist(qn, b, histtype="step", color="#e8710a", lw=1.5,
           label="gate %.0f-%.0f, ped %.0f-%.0f (as LNGS): median %.0f"
                 % (240 + SHIFT, 500 + SHIFT, SHIFT, 150 + SHIFT, np.median(qn)))
    for k, x in enumerate(MUONI_LNGS):
        a.axvline(x, color="#d62728", lw=1.6, ls="--", label="the 3 LNGS muon pulses" if k == 0 else None)
    a.set_xlabel("PMT charge, absolute value [ADC·ns]")
    a.set_ylabel("events / bin")
    a.set_title("Frascati ch%d, 3 SiPM > 500000 (%d ev.)\nscale: %.0f ADC·ns/PE (old) → %.0f (LNGS gate); LNGS muons: %.0f"
                % (ch, sel.sum(), np.median(qv) / PE_MU_MC, np.median(qn) / PE_MU_MC,
                   np.mean(MUONI_LNGS) / PE_MU_MC), fontsize=9.5, loc="left")
    a.legend(fontsize=7.5, frameon=False)
    a.grid(alpha=0.25)
fig.suptitle("Frascati muons integrated with the LNGS 0011 gate, aligned on the pulse (+%.0f ns)" % SHIFT, fontsize=11)
fig.tight_layout()
fig.savefig(OUT, dpi=100)
