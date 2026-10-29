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
col = {16: "#2a6fdb", 17: "#1aa39a", 18: "#8a5cd6"}
L = np.array([25, 50, 75, 100, 150, 200, 300, 400, 600, 800, 1000, 1148])

fig, ax = plt.subplots(1, 3, figsize=(17, 4.8), width_ratios=[1.2, 1, 1])
for j, ch in enumerate(c):
    y = d[:, j, :].astype(float)
    cel = (k[None, :] + s[:, ch // 8][:, None]) % 1024
    tab, _ = padme.mediana_per_gruppo(cel[:1000], y[:1000], 1024)
    y = y - np.nan_to_num(tab - np.nanmedian(tab))[cel]
    ped = y[:, t < 150].mean(1, keepdims=True)
    z = y - ped
    quieti = np.abs(z[:, t >= 200]).max(1) < 15
    i0 = int(np.searchsorted(t, 200))
    sig = []
    for l in L:
        n = int(round(l / dt))
        q = z[quieti, i0:i0 + n].sum(1) * dt
        sig.append(padme.sigma_robusta(q))
    ax[0].plot(L, sig, "o-", color=col[ch], label="ch%d (%d events)" % (ch, quieti.sum()))
    if j == 0:
        for l, a, tit in ((75, ax[1], "75 ns window (like the 'blank')"),
                          (1148, ax[2], "1150 ns window (like the gate 200-end)")):
            n = int(round(l / dt))
            q = z[quieti, i0:i0 + n].sum(1) * dt
            a.hist(q, 100, color=col[ch], alpha=0.8)
            a.set_title("ch%d, quiet events: charge in %s\nσ = %.0f ADC·ns"
                        % (ch, tit, padme.sigma_robusta(q)), fontsize=9.5, loc="left")
            a.set_xlabel("charge [ADC·ns]")
            a.grid(alpha=0.25)
        ax[1].set_xlim(-12000, 12000)
        ax[2].set_xlim(-12000, 12000)
ax[0].axvline(75, color="#888", ls="--", lw=1)
ax[0].text(80, ax[0].get_ylim()[1] * 0.9, "'blank' of the plot\n(75 ns)", fontsize=8, color="#555")
ax[0].axvline(1148, color="#e8710a", ls="--", lw=1)
ax[0].text(1140, ax[0].get_ylim()[1] * 0.5, "gate\n200-end", fontsize=8, color="#e8710a", ha="right")
ax[0].set_xlabel("integration window length, starting at 200 ns [ns]")
ax[0].set_ylabel("σ of the charge [ADC·ns]")
ax[0].set_title("Noise on the charge vs window length\n"
                "(pedestal 0-150 ns, events with no pulse > 15 counts after 200 ns)",
                fontsize=9.5, loc="left")
ax[0].grid(alpha=0.25)
ax[0].legend(fontsize=8)
fig.suptitle("run 0011 SiPM — same events, same pedestal, only the window length changes", fontsize=11)
fig.tight_layout()
fig.savefig(out, dpi=100)
