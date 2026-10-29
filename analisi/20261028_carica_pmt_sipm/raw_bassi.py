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

f51, f54, out = sys.argv[1], sys.argv[2], sys.argv[3]
CH = [16, 17, 18]
dt = 4 / 3
d51 = padme.leggi(f51, CH, None)[2].astype(float)
d54 = padme.leggi(f54, CH, None)[2].astype(float)
ns = d51.shape[2]
t = np.arange(ns) * dt
g = t >= 200

fig = plt.figure(figsize=(18, 13))
gs = fig.add_gridspec(4, 3, height_ratios=[1, 1.15, 1.15, 1.15])
for r, ch in enumerate(CH):
    a = fig.add_subplot(gs[0, r])
    q51 = d51[:, r, g].sum(1) * dt
    q54 = d54[:, r, g].sum(1) * dt
    b = np.linspace(min(q51.min(), q54.min()) - 2000, np.percentile(q54, 99.5), 160)
    a.hist(q54, b, weights=np.full(len(q54), 1 / len(q54)), histtype="step", color="#2a6fdb", lw=1.4,
           label="54 V (0011): min %.0f, median %.0f" % (q54.min(), np.median(q54)))
    a.hist(q51, b, weights=np.full(len(q51), 1 / len(q51)), histtype="step", color="#d62728", lw=1.4,
           label="51 V: min %.0f, median %.0f" % (q51.min(), np.median(q51)))
    a.set_yscale("log")
    a.set_xlabel("Σ ADC × Δt, 200-1350 ns, nothing subtracted [ADC·ns]")
    a.set_ylabel("fraction of events / bin")
    a.set_title("ch%d  raw integral (absolute, not shifted)" % ch, fontsize=9.5, loc="left")
    a.legend(fontsize=7.5, frameon=False)
    a.grid(alpha=0.25, which="both")

    # i 5 eventi piu' bassi e 5 attorno alla mediana, a 54 V
    ordine = np.argsort(q54)
    bassi = ordine[:5]
    medi = ordine[len(ordine) // 2 - 2: len(ordine) // 2 + 3]
    livello = np.median(d54[:, r, g])
    a = fig.add_subplot(gs[1 + r, :])
    for k_, e in enumerate(medi):
        a.plot(t, d54[e, r], lw=0.5, color="#999", alpha=0.8,
               label="5 events at the median (grey)" if k_ == 0 else None)
    cols = ["#d62728", "#ff7f0e", "#9467bd", "#8c564b", "#e377c2"]
    for k_, e in enumerate(bassi):
        a.plot(t, d54[e, r], lw=0.7, color=cols[k_],
               label="ev %d: integral %.0f = median %+.0f" % (e, q54[e], q54[e] - np.median(q54)))
    a.axhline(livello, color="k", lw=0.8, ls="--", label="median level of all samples %.1f counts" % livello)
    a.axhline(0, color="k", lw=1.2)
    a.axvspan(200, t[-1], color="#e8710a", alpha=0.06)
    a.set_ylim(livello - 25, livello + 40)
    a.set_xlabel("time [ns]")
    a.set_ylabel("raw ADC [counts]")
    a.set_title("ch%d  54 V: the 5 events with the LOWEST raw integral, raw ADC (nothing subtracted); "
                "zero of the ADC is far below (y-axis cut)" % ch, fontsize=9.5, loc="left")
    a.legend(fontsize=7.5, ncol=3, loc="upper right")
    a.grid(alpha=0.25)
    print(ch, "min 54 %.0f min 51 %.0f" % (q54.min(), q51.min()), "bassi", bassi.tolist())
fig.suptitle("SiPM raw integrals: always positive (~%.0f counts × 1150 ns); "
             "'negative' in the previous plot meant 'below the median'" % np.median(d54[:, 0, g]), fontsize=11)
fig.tight_layout()
fig.savefig(out, dpi=95)
