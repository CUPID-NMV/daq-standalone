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

out = sys.argv[1]
fra = np.load(ROOT + "/measurements/carica_frascati_0005_p0-150_g150-600.npz")
sel = np.load(ROOT + "/measurements/frascati_0005_sel_3sipm_500k.npy")
lng = np.load(ROOT + "/measurements/carica_0011_testbeam.npz")
q20, q21 = lng["q_caen_ch20"], lng["q_caen_ch21"]

# sigma del piedistallo: il picco attorno a zero della carica nel cancello
# 240-500 ns, dagli eventi in cui quel PMT non ha impulso
sig = {}
for ch, q in ((20, q20), (21, q21)):
    vicino = q[np.abs(q) < 1500]
    sig[ch] = padme.sigma_robusta(vicino)
    print("ch%d sigma piedistallo %.0f ADC*ns, 3 sigma = %.0f, eventi nel picco %d"
          % (ch, sig[ch], 3 * sig[ch], vicino.size))
both = (q20 > 3 * sig[20]) & (q21 > 3 * sig[21])
print("entrambi sopra 3 sigma:", both.sum(), "di", len(q20))
muoni = {20: [5827], 21: [3633, 4602]}

b = np.linspace(-3000, 100000, 260)
fig, ax = plt.subplots(1, 3, figsize=(21, 6.3), width_ratios=[1.1, 1.1, 0.8])
for a in ax[:2]:
    W = lambda x: np.full(len(x), 1.0 / len(x))
    a.hist(fra["q_caen_ch24"][sel], b, weights=W(fra["q_caen_ch24"][sel]), histtype="step", color="#2a6fdb", lw=1.2,
           label="Frascati 0005 ch24, 3 SiPM > 500000 (%d ev.)" % sel.sum())
    a.hist(fra["q_caen_ch26"][sel], b, weights=W(fra["q_caen_ch26"][sel]), histtype="step", color="#1aa39a", lw=1.2,
           label="Frascati 0005 ch26, 3 SiPM > 500000 (%d ev.)" % sel.sum())
    a.hist(q20, b, weights=W(q20), histtype="step", color="#e8710a", lw=0.9, ls=":",
           label="LNGS 0011 ch20, all %d ev." % len(q20))
    a.hist(q21, b, weights=W(q21), histtype="step", color="#c23b5a", lw=0.9, ls=":",
           label="LNGS 0011 ch21, all %d ev." % len(q21))
    a.hist(q20[both], b, weights=W(q20[both]), histtype="step", color="#e8710a", lw=1.6,
           label="LNGS 0011 ch20, BOTH PMT > 3σ_ped (%d ev.)" % both.sum())
    a.hist(q21[both], b, weights=W(q21[both]), histtype="step", color="#c23b5a", lw=1.6,
           label="LNGS 0011 ch21, BOTH PMT > 3σ_ped (%d ev.)" % both.sum())
    for ch, cc, ls in ((20, "#e8710a", "-"), (21, "#c23b5a", "--")):
        for k, x in enumerate(muoni[ch]):
            a.axvline(x, color=cc, lw=2.2, ls=ls, label="LNGS 0000 muon, ch%d" % ch if k == 0 else None)
    a.set_xlabel("PMT charge [ADC·ns]   (1 pC = 204.8 ADC·ns)")
    a.set_ylabel("fraction of events / bin (bin = %.0f ADC·ns), area = 1" % (b[1] - b[0]))
    a.grid(alpha=0.25, which="both")
ax[0].set_yscale("log")
ax[0].set_ylim(1e-5, 0.3)
ax[0].set_title("log scale", fontsize=10, loc="left")
ax[1].set_xlim(-3000, 30000)
ax[1].set_title("linear, zoom 0-30000", fontsize=10, loc="left")
ax[1].legend(fontsize=7.2, frameon=False, loc="upper right")

a = ax[2]
bb = np.linspace(-800, 1500, 116)
for ch, q, cc in ((20, q20, "#e8710a"), (21, q21, "#c23b5a")):
    a.hist(q, bb, histtype="step", color=cc, lw=1.3, label="ch%d: σ_ped = %.0f ADC·ns" % (ch, sig[ch]))
    a.axvline(3 * sig[ch], color=cc, ls="--", lw=1.2, label="ch%d: 3σ = %.0f" % (ch, 3 * sig[ch]))
a.set_yscale("log")
a.set_xlabel("PMT charge [ADC·ns], gate 240-500 ns")
a.set_ylabel("events / bin")
a.set_title("LNGS 0011: pedestal peak and the 3σ cut", fontsize=10, loc="left")
a.legend(fontsize=8, frameon=False)
a.grid(alpha=0.25, which="both")
fig.suptitle("AREA-NORMALISED: LNGS 0011 (all, and both PMTs > 3σ_ped) vs Frascati 0005 (3 SiPM cut); vertical lines: the two LNGS muons",
             fontsize=11)
fig.tight_layout()
fig.savefig(out, dpi=100)
