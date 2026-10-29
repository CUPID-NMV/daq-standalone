# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
_sys.path.insert(0, _os.path.join(ROOT, "tools"))
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fra = np.load(ROOT + "/measurements/carica_frascati_0005_p0-150_g150-600.npz")
lng = np.load(ROOT + "/measurements/carica_0011_testbeam.npz")
out = sys.argv[1]
sel = np.load(ROOT + "/measurements/frascati_0005_sel_3sipm_500k.npy")
# Per tutti e quattro la carica con la sola correzione CAEN: a Frascati la
# PADME non c'e', e cosi' il confronto e' alla pari.
serie = [
    (fra["q_caen_ch24"][sel], "Frascati 0005 ch24, 3 SiPM > 500000 (gate 150-600 ns)", "#2a6fdb", "-"),
    (fra["q_caen_ch26"][sel], "Frascati 0005 ch26, 3 SiPM > 500000 (gate 150-600 ns)", "#1aa39a", "-"),
    (lng["q_caen_ch20"], "LNGS 0011 ch20 (gate 240-500 ns)", "#e8710a", "-"),
    (lng["q_caen_ch21"], "LNGS 0011 ch21 (gate 240-500 ns)", "#c23b5a", "-"),
]
b = np.linspace(-3000, 100000, 260)
fig, ax = plt.subplots(1, 2, figsize=(17, 6))
for q, lab, col, ls in serie:
    for a in ax:
        a.hist(q, b, histtype="step", color=col, lw=1.4, ls=ls,
               label="%s: %d events, median %.0f" % (lab, len(q), np.median(q)))
ax[0].set_yscale("log")
ax[1].set_xlim(-3000, 40000)
for a, tit in ((ax[0], "log scale"), (ax[1], "linear scale, zoom 0-40000")):
    a.set_xlabel("charge [ADC·ns]   (1 pC = 204.8 ADC·ns)")
    a.set_ylabel("events / bin (bin = %.0f ADC·ns), NOT normalised" % (b[1] - b[0]))
    a.set_title("PMT charge, pedestal 0-150 ns, CAEN correction only — %s" % tit, fontsize=10, loc="left")
    a.grid(alpha=0.25, which="both")
    a.legend(fontsize=8, frameon=False)
fig.suptitle("PMT charge spectra: Frascati run 0005 with all three SiPM > 500000 ADC·ns raw (35053 of 77382 ev.) vs "
             "LNGS run 0011 (5337 ev., 3.3 h, self-trigger OR)", fontsize=11)
fig.tight_layout()
fig.savefig(out, dpi=100)
