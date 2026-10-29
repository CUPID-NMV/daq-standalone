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
CH = [16, 17, 18]
# 1 p.e. a 54 V misurato al LNGS sulla run 0039 (CLAUDE.md): 2.75 / 3.02 / 2.92 mV
PE = np.array([2.75, 3.02, 2.92]) / (1000.0 / 4096)
h, c, d, s = padme.leggi(f, CH, None)
d = d.astype(float)
n, _, ns = d.shape
dt = 4 / 3
t = np.arange(ns) * dt
z = d - d[:, :, t < 150].mean(2, keepdims=True)
amp = z.max(2)
t50 = np.array([[t[np.argmax(z[e, j] >= 0.5 * amp[e, j])] for j in range(3)] for e in range(n)])
ok = (amp / PE[None, :] >= 4) & (t50 >= 330) & (t50 <= 560)
col = ["#2a6fdb", "#1aa39a", "#8a5cd6"]
fig, ax = plt.subplots(8, 5, figsize=(20, 24), sharex=True)
for e, a in zip(range(n), ax.ravel()):
    for j, ch in enumerate(CH):
        y = z[e, j] / PE[j]
        a.plot(t, y, lw=0.6, color=col[j],
               label="ch%d %s %.0f pe @%.0f" % (ch, "SIG" if ok[e, j] else "dc?", amp[e, j] / PE[j], t50[e, j]))
    a.axvspan(330, 560, color="#e8710a", alpha=0.07)
    a.set_ylim(-8, min(max(60, 1.15 * (amp[e] / PE).max()), 400))
    a.set_title("ev %d   SiPM signals: %d of 3" % (e, ok[e].sum()), fontsize=8.5,
                color="#d62728" if ok[e].sum() <= 1 else "k")
    a.legend(fontsize=6.5, loc="upper right", frameon=False)
    a.grid(alpha=0.25)
for a in ax.ravel()[n:]:
    a.axis("off")
for a in ax[:, 0]:
    a.set_ylabel("photoelectrons")
fig.suptitle("LNGS 0000: the three SiPM of the 38 events in photoelectrons (1 p.e. = 2.75/3.02/2.92 mV, run 0039). "
             "SIG = ≥ 4 p.e. with the 50%% crossing in 330-560 ns (orange band); dc? = otherwise", fontsize=11)
fig.tight_layout()
fig.savefig(out, dpi=78)
for e in range(n):
    print(e, "".join("S" if o else "." for o in ok[e]), " ".join("%.0f@%.0f" % (amp[e, j] / PE[j], t50[e, j]) for j in range(3)))
