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
CH = [16, 17, 18, 20, 21]
h, c, d, s = padme.leggi(f, CH, None)
d = d.astype(float)
n, _, ns = d.shape
dt = 4 / 3
t = np.arange(ns) * dt
p = t < 150
segno = np.array([1, 1, 1, -1, -1])
z = segno[None, :, None] * (d - d[:, :, p].mean(2, keepdims=True))
amp = z.max(2)
sat = ((d <= 0) | (d >= 4095)).any(2)
tmax = t[z.argmax(2)]
q_pmt = z[:, 3:, (t >= 240) & (t < 500)].sum(2) * dt       # come la 0011
q_sipm = z[:, :3, t >= 200].sum(2) * dt
print("ev  " + "  ".join("ch%d amp[cnt] t[ns]" % ch for ch in CH))
for e in range(n):
    print("%2d  " % e + "  ".join("%7.0f%s %5.0f" % (amp[e, j], "*" if sat[e, j] else " ", tmax[e, j])
                                   for j in range(5)),
          " qPMT %.0f %.0f" % tuple(q_pmt[e]))
np.savez(ROOT + "/measurements/lngs_0000_eventi.npz", amp=amp, sat=sat, tmax=tmax, q_pmt=q_pmt, q_sipm=q_sipm)

fig, ax = plt.subplots(8, 5, figsize=(20, 22), sharex=True)
col = ["#2a6fdb", "#1aa39a", "#8a5cd6", "#e8710a", "#c23b5a"]
for e, a in zip(range(n), ax.ravel()):
    for j, ch in enumerate(CH):
        a.plot(t, z[e, j] / max(amp[e, j], 1), lw=0.6, color=col[j], label="ch%d" % ch)
    a.set_title("ev %d  amp: %s" % (e, " ".join("%.0f" % x for x in amp[e])), fontsize=7.5)
    a.set_ylim(-0.4, 1.1)
    a.grid(alpha=0.25)
for a in ax.ravel()[n:]:
    a.axis("off")
ax[0, 0].legend(fontsize=6, ncol=5)
fig.suptitle("LNGS 0000 (23.4 h, V812 majority 4 of 5): all 38 events, each channel normalised to its own max "
             "(amplitudes in counts in the titles: ch16 ch17 ch18 ch20 ch21)", fontsize=11)
fig.tight_layout()
fig.savefig(out, dpi=80)
