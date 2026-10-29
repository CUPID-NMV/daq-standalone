# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
_sys.path.insert(0, _os.path.join(ROOT, "tools"))
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
import padme

f, out = sys.argv[1], sys.argv[2]
lungo = np.load(ROOT + "/measurements/carica_0011_sipm_g200-fine.npz")
corto = np.load(ROOT + "/measurements/carica_0011_sipm_g240-500.npz")
h, c, d, s = padme.leggi(f, [16, 17, 18], None)
ns = d.shape[2]
dt = 4 / 3
t = np.arange(ns) * dt
k = np.arange(ns)
ip = t < 150
col = {16: "#2a6fdb", 17: "#1aa39a", 18: "#8a5cd6"}

fig, ax = plt.subplots(3, 3, figsize=(17, 11.5), width_ratios=[1.3, 1, 1.2])
for riga, (j, ch) in zip(ax, enumerate(c)):
    ql, qc = lungo["q_padme_ch%d" % ch], corto["q_padme_ch%d" % ch]

    a = riga[0]
    lo, hi = np.percentile(np.concatenate([ql, qc]), [0.3, 99.7])
    b = np.linspace(lo, hi, 220)
    a.hist(qc, b, histtype="step", color="#888", lw=1.1,
           label="gate 240-500 ns: median %.0f" % np.median(qc))
    a.hist(ql, b, histtype="step", color=col[ch], lw=1.4,
           label="gate 200-1350 ns: median %.0f" % np.median(ql))
    a.set_yscale("log")
    a.set_xlabel("charge [ADC·ns]  (1 pC = 204.8)")
    a.set_ylabel("events / bin")
    a.set_title("ch%d  charge, pedestal 0-150 ns, PADME test-beam" % ch, fontsize=9.5, loc="left")
    a.legend(fontsize=8, frameon=False)
    a.grid(alpha=0.25, which="both")

    a = riga[1]
    lim = np.percentile(np.concatenate([ql, qc]), [0.3, 99.5])
    hh = a.hist2d(qc, ql, bins=120, range=[lim, lim], norm=LogNorm(), cmap="viridis")
    a.plot(lim, lim, color="#e8710a", lw=0.8, ls="--", label="y = x")
    a.set_xlabel("charge 240-500 ns [ADC·ns]")
    a.set_ylabel("charge 200-1350 ns [ADC·ns]")
    a.set_title("ch%d  same event, two gates" % ch, fontsize=9.5, loc="left")
    a.legend(fontsize=8, loc="upper left")

    # traccia media: tutti gli eventi e gli eventi senza impulso nel canale
    y = d[:, j, :].astype(float)
    sic = s[:, ch // 8]
    cel = (k[None, :] + sic[:, None]) % 1024
    tab, _ = padme.mediana_per_gruppo(cel[:1000], y[:1000], 1024)
    y = y - np.nan_to_num(tab - np.nanmedian(tab))[cel]
    y = y - y[:, ip].mean(1, keepdims=True)
    quieti = np.abs(y[:, t >= 200]).max(1) < 15
    a = riga[2]
    a.plot(t, y.mean(0), color=col[ch], lw=0.9, label="all %d events" % len(y))
    a.plot(t, y[quieti].mean(0), color="#333", lw=0.9,
           label="%d events with no sample > 15 counts after 200 ns" % quieti.sum())
    a.axvspan(0, 150, color="#888", alpha=0.2, label="pedestal 0-150")
    a.axvspan(200, t[-1], color="#e8710a", alpha=0.08, label="gate 200-end")
    a.axhline(0, color="#888", lw=0.7)
    a.set_ylim(-3, 12)
    a.set_xlabel("time [ns]")
    a.set_ylabel("[counts]")
    a.set_title("ch%d  mean trace, pedestal subtracted" % ch, fontsize=9.5, loc="left")
    a.legend(fontsize=7.5, loc="upper right")
    a.grid(alpha=0.25)
    print(ch, "quieti", quieti.sum(), "livello medio dei quieti 200-fine %.2f" % y[quieti][:, t >= 200].mean(),
          "carica dei quieti: mediana %.0f rms %.0f" % (np.median(ql[quieti]), np.std(ql[quieti])))
fig.suptitle("run 0011 SiPM — gate 200 ns → end of trace vs 240-500 ns", fontsize=11)
fig.tight_layout()
fig.savefig(out, dpi=95)
