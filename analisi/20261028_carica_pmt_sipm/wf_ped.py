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

f, ch, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
h, c, d, s = padme.leggi(f, [ch], None)
ns = d.shape[2]
dt = 4 / 3
t = np.arange(ns) * dt
sic = s[:, ch // 8]
y = d[:, 0, :].astype(float)
ip = int(150 / dt)
cel = (np.arange(ns)[None, :] + sic[:, None]) % 1024
tab, _ = padme.mediana_per_gruppo(cel[:1000], y[:1000], 1024)  # ricetta test beam
tab = np.nan_to_num(tab - np.nanmedian(tab))
yp = y - tab[cel]

# Solo piedistallo: nel cancello 240-500 nessun campione oltre 10 conteggi
# dal piedistallo dell'evento, in nessuno dei due versi.
g = (t >= 240) & (t < 500)
z = yp - yp[:, :ip].mean(1, keepdims=True)
ped = np.where(np.abs(z[:, g]).max(1) < 10)[0]
rng = np.random.default_rng(1)
scelti = np.sort(rng.choice(ped, 20, replace=False))
print("ch%d: %d eventi di solo piedistallo su %d" % (ch, len(ped), len(y)))

fig, ax = plt.subplots(5, 4, figsize=(17, 13), sharex=True, sharey=True)
for a, e in zip(ax.ravel(), scelti):
    yc = y[e] - y[e, :ip].mean()
    ypc = yp[e] - yp[e, :ip].mean()
    a.plot(t, yc, lw=0.7, color="#e8710a", label="CAEN only  rms %.2f" % yc.std())
    a.plot(t, ypc, lw=0.7, color="#2a6fdb", label="+ PADME  rms %.2f" % ypc.std())
    a.axvspan(0, 150, color="#888", alpha=0.15)
    a.axvspan(240, 500, color="#e8710a", alpha=0.08)
    a.set_title("event %d  (SIC %d)" % (e, sic[e]), fontsize=8.5)
    a.legend(fontsize=7, loc="upper right", frameon=False)
    a.grid(alpha=0.25)
a.set_ylim(-12, 12)
for a in ax[-1]:
    a.set_xlabel("time [ns]")
for a in ax[:, 0]:
    a.set_ylabel("ADC − pedestal(0-150 ns) [counts]")
fig.suptitle("run 0011, ch%d: 20 pedestal-only events (|signal| < 10 counts in 240-500 ns), "
             "same trace with and without PADME (test-beam recipe) — grey: pedestal 0-150 ns, orange band: gate 240-500 ns"
             % ch, fontsize=10)
fig.tight_layout()
fig.savefig(out, dpi=100)
