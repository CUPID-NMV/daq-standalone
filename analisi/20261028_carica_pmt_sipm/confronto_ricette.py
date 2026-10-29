# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
_sys.path.insert(0, _os.path.join(ROOT, "tools"))
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
sys.path.insert(0, ".")
import padme

tb = np.load(sys.argv[1])
pd = np.load(sys.argv[2])
out = sys.argv[3]
canali = [int(c) for c in tb["canali"]]
fig, ax = plt.subplots(len(canali), 3, figsize=(17, 3.2 * len(canali)), width_ratios=[1.5, 1, 1])
for riga, ch in zip(ax, canali):
    qc, qt, qp = tb["q_caen_ch%d" % ch], tb["q_padme_ch%d" % ch], pd["q_padme_ch%d" % ch]
    vc, vt, vp = tb["vuoto_caen_ch%d" % ch], tb["vuoto_padme_ch%d" % ch], pd["vuoto_padme_ch%d" % ch]
    a = riga[0]
    lo, hi = np.percentile(np.concatenate([qc, qt, qp]), [0.2, 99.8])
    b = np.linspace(lo - 0.05 * (hi - lo), hi, 200)
    a.hist(qc, b, histtype="step", color="#888", lw=1.1, label="CAEN only")
    a.hist(qp, b, histtype="step", color="#2a6fdb", lw=1.1, label="+ PADME, pedestal-window recipe")
    a.hist(qt, b, histtype="step", color="#e8710a", lw=1.3, label="+ PADME, test-beam recipe")
    a.set_yscale("log")
    a.set_title("ch%d  charge in gate 240-500 ns (pedestal 0-150 ns)" % ch, fontsize=9.5, loc="left")
    a.set_xlabel("charge [ADC·ns]")
    a.set_ylabel("events / bin")
    a.legend(fontsize=7.5, frameon=False)
    a.grid(alpha=0.25, which="both")

    a = riga[1]
    s = [padme.sigma_robusta(v) for v in (vc, vp, vt)]
    lo, hi = np.percentile(np.concatenate([vc, vt, vp]), [0.5, 99.5])
    b = np.linspace(lo, hi, 80)
    a.hist(vc, b, histtype="step", color="#888", lw=1.1, label="CAEN only  σ = %.0f" % s[0])
    a.hist(vp, b, histtype="step", color="#2a6fdb", lw=1.1, label="pedestal-window  σ = %.0f" % s[1])
    a.hist(vt, b, histtype="step", color="#e8710a", lw=1.3, label="test-beam  σ = %.0f" % s[2])
    a.set_title("ch%d  blank charge (75 ns, pedestal-only region)" % ch, fontsize=9.5, loc="left")
    a.set_xlabel("charge [ADC·ns]")
    a.legend(fontsize=7.5, frameon=False)
    a.grid(alpha=0.25)

    a = riga[2]
    d1, d2 = qt - qc, qt - qp
    lo, hi = np.percentile(np.concatenate([d1, d2]), [0.5, 99.5])
    b = np.linspace(lo, hi, 80)
    a.hist(d1, b, histtype="step", color="#888", lw=1.1,
           label="test-beam − CAEN only: mean %.0f, rms %.0f" % (d1.mean(), d1.std()))
    a.hist(d2, b, histtype="step", color="#2a6fdb", lw=1.1,
           label="test-beam − pedestal-window: mean %.0f, rms %.0f" % (d2.mean(), d2.std()))
    a.set_title("ch%d  same event, charge difference" % ch, fontsize=9.5, loc="left")
    a.set_xlabel("Δ charge [ADC·ns]")
    a.legend(fontsize=7, frameon=False)
    a.grid(alpha=0.25)
fig.suptitle("run 0011 — charge with the test-beam PADME recipe vs pedestal-window recipe vs CAEN only",
             fontsize=11)
fig.tight_layout()
fig.savefig(out, dpi=95)
