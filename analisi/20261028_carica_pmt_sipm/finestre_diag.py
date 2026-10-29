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

f = sys.argv[1]
h, c, d, s = padme.leggi(f, [16, 17, 18, 20, 21], None)
ns = d.shape[2]
t = np.arange(ns) * 4 / 3
sic = s[:, 2]
p = t < 150
g = (t >= 240) & (t < 500)
col = {16: "#2a6fdb", 17: "#1aa39a", 18: "#8a5cd6", 20: "#e8710a", 21: "#c23b5a"}

fig, ax = plt.subplots(2, 2, figsize=(14, 8.5))
for j, ch in enumerate(c):
    y = d[:, j, :].astype(float)
    tab, _ = padme.mediana_per_gruppo((np.arange(150 * 3 // 4)[None, :] + sic[:, None]) % 1024,
                                      y[:, :150 * 3 // 4], 1024)
    tab = np.nan_to_num(tab - np.nanmedian(tab))
    y = y - tab[(np.arange(ns)[None, :] + sic[:, None]) % 1024]
    y = y - y[:, p].mean(1, keepdims=True)
    if ch >= 20:
        y = -y
    q = y[:, g].sum(1)
    m = y[q >= np.percentile(q, 80)].mean(0)
    a = ax[0, 0] if ch < 20 else ax[0, 1]
    a.plot(t, m, lw=0.9, color=col[ch], label="ch%d" % ch)
    if ch < 20:
        cum = np.cumsum(m[t >= 240]) / m[(t >= 240) & (t < 1300)].sum()
        ax[1, 0].plot(t[t >= 240], cum, color=col[ch], lw=1.3, label="ch%d" % ch)
    # piedistallo medio su TUTTI gli eventi, primi 300 ns
    ax[1, 1].plot(t[t < 300], y[:, t < 300].mean(0), lw=0.9, color=col[ch], label="ch%d" % ch)

for a, tit in ((ax[0, 0], "SiPM: mean waveform, 20% highest events (PADME, ped 0-150 ns)"),
               (ax[0, 1], "PMT: mean waveform, 20% highest events (sign-corrected)")):
    a.axvspan(0, 150, color="#888", alpha=0.2, label="pedestal 0-150")
    a.axvspan(240, 500, color="#e8710a", alpha=0.15, label="gate 240-500")
    a.set_title(tit, fontsize=10, loc="left")
    a.set_xlabel("time [ns]")
    a.set_ylabel("[counts]")
    a.grid(alpha=0.25)
    a.legend(fontsize=8)
ax[0, 1].set_yscale("symlog", linthresh=5)

a = ax[1, 0]
a.axvline(500, color="#e8710a", ls="--", lw=1)
a.axhline(1, color="#888", lw=0.8)
for x in (500, 700, 900, 1100):
    a.axvline(x, color="#ccc", lw=0.6)
a.set_title("SiPM: fraction of the mean pulse area (240-1300 ns) inside 240 ns → gate end",
            fontsize=10, loc="left")
a.set_xlabel("gate end [ns]")
a.set_ylabel("fraction")
a.set_ylim(0, 1.05)
a.grid(alpha=0.25)
a.legend(fontsize=8)

a = ax[1, 1]
a.axvspan(0, 150, color="#888", alpha=0.2)
a.axvline(240, color="#e8710a", ls="--", lw=1)
a.axhline(0, color="#888", lw=0.8)
a.set_title("All events: mean trace, first 300 ns (PADME, ped 0-150 subtracted,\n"
            "PMT sign-corrected: positive = direction of the pulse)", fontsize=10, loc="left")
a.set_xlabel("time [ns]")
a.set_ylabel("[counts]")
a.set_ylim(-4, 6)
a.grid(alpha=0.25)
a.legend(fontsize=8, ncol=5)
fig.suptitle("run 0011 — pedestal 0-150 ns, gate 240-500 ns", fontsize=11)
fig.tight_layout()
fig.savefig(sys.argv[2], dpi=110)
