# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
_sys.path.insert(0, _os.path.join(ROOT, "tools"))
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

out = sys.argv[1]
fra = np.load(ROOT + "/measurements/carica_frascati_0005_p0-150_g150-600.npz")
sel = np.load(ROOT + "/measurements/frascati_0005_sel_3sipm_500k.npy")
lng = np.load(ROOT + "/measurements/carica_0011_testbeam.npz")
l24 = np.load(ROOT + "/measurements/lngs_0000_eventi.npz")
amp, q = l24["amp"], l24["q_pmt"]
mu = np.all(amp[:, :3] > 3000, axis=1)          # tutti e tre i SiPM saturi, come i muoni di Frascati
print("muoni", np.where(mu)[0].tolist(), q[mu].tolist())

b = np.linspace(-3000, 100000, 260)
fig, ax = plt.subplots(1, 2, figsize=(18, 6.5))
for a in ax:
    a.hist(fra["q_caen_ch24"][sel], b, histtype="step", color="#2a6fdb", lw=1.3,
           label="Frascati 0005 ch24, 3 SiPM > 500000 (%d ev.)" % sel.sum())
    a.hist(fra["q_caen_ch26"][sel], b, histtype="step", color="#1aa39a", lw=1.3,
           label="Frascati 0005 ch26, 3 SiPM > 500000 (%d ev.)" % sel.sum())
    a.hist(lng["q_caen_ch20"], b, histtype="step", color="#e8710a", lw=1.3,
           label="LNGS 0011 ch20, self-trigger OR (%d ev.)" % len(lng["q_caen_ch20"]))
    a.hist(lng["q_caen_ch21"], b, histtype="step", color="#c23b5a", lw=1.3,
           label="LNGS 0011 ch21, self-trigger OR (%d ev.)" % len(lng["q_caen_ch21"]))
    # gli altri eventi della run da 24 h: tacche in basso
    for j, cc in ((0, "#e8710a"), (1, "#c23b5a")):
        a.plot(q[~mu, j], np.full((~mu).sum(), 0.8 if j == 0 else 0.65), "|", color=cc, ms=14, mew=1.2,
               label="LNGS 0000 (24 h) ch%d, the other %d events (not muon-like)" % (20 + j, (~mu).sum()))
    for e in np.where(mu)[0]:
        for j, cc, ls in ((0, "#e8710a", "-"), (1, "#c23b5a", "--")):
            a.axvline(q[e, j], color=cc, lw=2.5, ls=ls,
                      label="LNGS 0000 MUON ev %d, ch%d: %.0f" % (e, 20 + j, q[e, j]))
    a.set_xlabel("PMT charge [ADC·ns]   (1 pC = 204.8 ADC·ns)")
    a.set_ylabel("events / bin (bin = %.0f ADC·ns), not normalised" % (b[1] - b[0]))
    a.grid(alpha=0.25, which="both")
ax[0].set_yscale("log")
ax[0].set_ylim(0.5, 5000)
ax[0].set_title("log scale", fontsize=10, loc="left")
ax[1].set_xlim(-3000, 30000)
ax[1].set_title("linear, zoom 0-30000", fontsize=10, loc="left")
ax[1].legend(fontsize=7.5, frameon=False, loc="upper right")
fig.suptitle("Where the LNGS muons sit: 24 h run LNGS 0000 (V812 majority 4 of 5), muon = all three SiPM saturated "
             "→ %d events. Pedestal 0-150 ns, CAEN correction only" % mu.sum(), fontsize=11)
fig.tight_layout()
fig.savefig(out, dpi=100)
