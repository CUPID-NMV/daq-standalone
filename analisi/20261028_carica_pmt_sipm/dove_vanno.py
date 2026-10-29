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

f, out = sys.argv[1], sys.argv[2]
h, c, d, s = padme.leggi(f, [16, 17, 18], None)
ns = d.shape[2]
dt = 4 / 3
t = np.arange(ns) * dt
k = np.arange(ns)
g = t >= 200
p = t < 150
SOGLIA = -5000

fig, ax = plt.subplots(3, 3, figsize=(17, 11.5))
for riga, (j, ch) in zip(ax, enumerate(c)):
    y = d[:, j, :].astype(float)
    cel = (k[None, :] + s[:, ch // 8][:, None]) % 1024
    tab, _ = padme.mediana_per_gruppo(cel[:1000], y[:1000], 1024)
    y = y - np.nan_to_num(tab - np.nanmedian(tab))[cel]
    raw = y[:, g].sum(1) * dt
    ped = y[:, p].mean(1)
    sub = (y[:, g] - ped[:, None]).sum(1) * dt
    sub0 = sub - np.median(sub)
    neg = sub0 < SOGLIA
    r0 = raw - np.median(raw)

    a = riga[0]
    lo, hi = np.percentile(np.concatenate([r0, sub0]), [0.2, 99.5])
    b = np.linspace(lo, hi, 200)
    a.hist(sub0, b, histtype="step", color="#888", lw=1.1, label="baseline subtracted, all")
    a.hist(sub0[neg], b, color="#d62728", alpha=0.7,
           label="baseline subtracted, < %d: %d events" % (SOGLIA, neg.sum()))
    a.hist(r0, b, histtype="step", color="#2a6fdb", lw=1.3, label="no subtraction, all")
    a.hist(r0[neg], b, color="#ff9f1c", alpha=0.8, label="no subtraction, SAME %d events" % neg.sum())
    a.set_yscale("log")
    a.set_xlabel("charge − median [ADC·ns], gate 200-1350 ns")
    a.set_ylabel("events / bin")
    a.set_title("ch%d  where the negative events go" % ch, fontsize=9.5, loc="left")
    a.legend(fontsize=7.5, frameon=False)
    a.grid(alpha=0.25, which="both")

    a = riga[1]
    pm = ped - np.median(ped)
    a.scatter(pm[~neg], r0[~neg], s=2, color="#2a6fdb", alpha=0.4, label="others")
    a.scatter(pm[neg], r0[neg], s=4, color="#d62728", label="negative after subtraction")
    a.set_xlim(np.percentile(pm, [0.1, 99.9]))
    a.set_ylim(np.percentile(r0, [0.1, 99.5]))
    a.set_xlabel("baseline 0-150 ns − its median [counts]")
    a.set_ylabel("raw charge − median [ADC·ns]")
    a.set_title("ch%d  raw charge vs baseline of the event" % ch, fontsize=9.5, loc="left")
    a.legend(fontsize=7.5, markerscale=3)
    a.grid(alpha=0.25)

    a = riga[2]
    ii = np.where(neg)[0][:6]
    for n_, e in enumerate(ii):
        a.plot(t, y[e] - np.median(ped) + 25 * n_, lw=0.6)
        a.text(t[-1], 25 * n_ + 3, "ev %d" % e, fontsize=7, ha="right")
    a.axvspan(0, 150, color="#888", alpha=0.2)
    a.set_xlabel("time [ns]")
    a.set_ylabel("ADC − median baseline [counts] (offset 25 per trace)")
    a.set_title("ch%d  first 6 of the red events (PADME, no subtraction)" % ch, fontsize=9.5, loc="left")
    a.grid(alpha=0.25)
    print(ch, "negativi", neg.sum(), "livello 0-150 dei negativi: mediana %.1f conteggi sopra la mediana" % np.median(pm[neg]),
          "carica grezza dei negativi: mediana %.0f" % np.median(r0[neg]))
fig.suptitle("run 0011 SiPM — events with charge < %d ADC·ns after baseline subtraction, followed in the raw charge" % SOGLIA,
             fontsize=11)
fig.tight_layout()
fig.savefig(out, dpi=95)
