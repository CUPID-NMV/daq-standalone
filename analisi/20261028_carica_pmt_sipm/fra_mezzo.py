"""Frascati 0005: eventi dei SiPM fra piedistallo e saturazione.
Categorie per SiPM, sull'integrale grezzo 200-fine:
  ped   integrale <= 500000
  mezzo integrale > 500000 e nessun campione a 4095
  sat   almeno un campione a 4095"""
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
SOGLIA = 500000.0
CH = [16, 17, 18]
h, c, d, s = padme.leggi(f, CH, None, serve_sic=False)
n, _, ns = d.shape
dt = 4 / 3
t = np.arange(ns) * dt
g = t >= 200
pmt = np.load(ROOT + "/measurements/carica_frascati_0005_p0-150_g150-600.npz")
qp = pmt["q_caen_ch24"] + pmt["q_caen_ch26"]

cat = np.zeros((n, 3), dtype=int)       # 0 ped, 1 mezzo, 2 sat
t50 = np.full((n, 3), np.nan)
amp = np.zeros((n, 3))
for j in range(3):
    y = d[:, j, :]
    raw = y[:, g].sum(1, dtype=np.int64) * dt
    sat = (y >= 4095).any(1)
    cat[:, j] = np.where(sat, 2, np.where(raw > SOGLIA, 1, 0))
    base = np.median(y[:, t < 150], axis=1)
    a = y.max(1) - base
    amp[:, j] = a
    # istante in cui l'impulso passa il 50% del suo massimo, primo campione
    sopra = (y - base[:, None]) >= 0.5 * a[:, None]
    t50[:, j] = t[np.argmax(sopra, axis=1)]
nomi = ["pedestal", "middle", "saturated"]
col = {16: "#2a6fdb", 17: "#1aa39a", 18: "#8a5cd6"}

fig = plt.figure(figsize=(18, 17))
gs = fig.add_gridspec(4, 3, height_ratios=[1, 1, 1, 1])
for j, ch in enumerate(CH):
    a = fig.add_subplot(gs[0, j])
    b = np.arange(0, 1350, 4 * dt)
    for k, cc, lab in ((1, "#d62728", "middle"), (2, "#888", "saturated")):
        m = cat[:, j] == k
        a.hist(t50[m, j], b, histtype="step", color=cc, lw=1.4, density=True,
               label="%s: %d events, median %.0f ns" % (lab, m.sum(), np.median(t50[m, j])))
    a.set_yscale("log")
    a.set_xlabel("time where the SiPM pulse crosses 50% of its maximum [ns]")
    a.set_ylabel("fraction / bin (normalised)")
    a.set_title("ch%d  pulse time: middle vs saturated" % ch, fontsize=9.5, loc="left")
    a.legend(fontsize=8, frameon=False)
    a.grid(alpha=0.25, which="both")

    a = fig.add_subplot(gs[2, j])
    m = cat[:, j] >= 1
    a.hist(amp[m, j], np.linspace(0, 3900, 200), color=col[ch], histtype="step", lw=1.3)
    a.axvline(4095 - np.median(np.median(d[:, j, t < 150], axis=1)), color="k", ls="--", lw=1,
              label="full scale (4095 − baseline)")
    a.set_yscale("log")
    a.set_xlabel("pulse amplitude above baseline [counts]  (1 count = 0.244 mV)")
    a.set_ylabel("events / bin")
    a.set_title("ch%d  amplitude, events with integral > %.0f" % (ch, SOGLIA), fontsize=9.5, loc="left")
    a.legend(fontsize=8)
    a.grid(alpha=0.25, which="both")

for k, (i, jj) in enumerate([(0, 1), (0, 2), (1, 2)]):
    a = fig.add_subplot(gs[1, k])
    M = np.zeros((3, 3), dtype=int)
    for u in range(3):
        for v in range(3):
            M[u, v] = np.sum((cat[:, i] == u) & (cat[:, jj] == v))
    a.imshow(M, cmap="Blues", norm=matplotlib.colors.LogNorm(vmin=1, vmax=M.max()))
    for u in range(3):
        for v in range(3):
            a.text(v, u, "%d\n(%.1f%%)" % (M[u, v], 100 * M[u, v] / n), ha="center", va="center",
                   fontsize=9, color="w" if M[u, v] > M.max() / 8 else "k")
    a.set_xticks(range(3)); a.set_xticklabels(nomi)
    a.set_yticks(range(3)); a.set_yticklabels(nomi)
    a.set_xlabel("ch%d" % CH[jj]); a.set_ylabel("ch%d" % CH[i])
    a.set_title("ch%d vs ch%d: category of the same event" % (CH[i], CH[jj]), fontsize=9.5, loc="left")

a = fig.add_subplot(gs[3, :2])
gruppi = [((cat == 2).all(1), "all three saturated", "#888"),
          (((cat >= 1).all(1)) & ((cat == 1).any(1)) & ~((cat == 1).all(1)), "all above threshold, 1-2 'middle'", "#ff7f0e"),
          ((cat == 1).all(1), "all three 'middle'", "#d62728"),
          ((cat == 0).all(1), "all three pedestal (no panel)", "#2a6fdb")]
bb = np.linspace(-3000, 160000, 220)
for m, lab, cc in gruppi:
    a.hist(qp[m], bb, histtype="step", color=cc, lw=1.4, density=True,
           label="%s: %d events, median %.0f" % (lab, m.sum(), np.median(qp[m]) if m.any() else 0))
a.set_yscale("log")
a.set_xlabel("PMT charge ch24 + ch26 [ADC·ns], ped 0-150, gate 150-600 ns")
a.set_ylabel("fraction / bin (normalised)")
a.set_title("WC (PMT) charge for different SiPM categories", fontsize=9.5, loc="left")
a.legend(fontsize=8, frameon=False)
a.grid(alpha=0.25, which="both")

a = fig.add_subplot(gs[3, 2])
nm = (cat == 1).sum(1)
ns_ = (cat == 2).sum(1)
np_ = (cat == 0).sum(1)
tab = {}
for e in range(n):
    key = "%dS %dM %dP" % (ns_[e], nm[e], np_[e])
    tab[key] = tab.get(key, 0) + 1
ks = sorted(tab, key=lambda k: -tab[k])
a.barh(range(len(ks)), [tab[k] for k in ks], color="#2a6fdb")
for i_, k_ in enumerate(ks):
    a.text(tab[k_], i_, " %d (%.1f%%)" % (tab[k_], 100 * tab[k_] / n), va="center", fontsize=8)
a.set_yticks(range(len(ks))); a.set_yticklabels(ks, fontsize=8)
a.invert_yaxis()
a.set_xscale("log")
a.set_xlabel("events")
a.set_title("events by number of panels\nS = saturated, M = middle, P = pedestal", fontsize=9.5, loc="left")
fig.suptitle("Frascati run 0005 — SiPM 'middle' events (raw integral > 500000, not saturated): "
             "timing, correlation between panels, amplitude, WC charge", fontsize=11)
fig.tight_layout()
fig.savefig(out, dpi=90)
for k_ in ks:
    print(k_, tab[k_])
for j, ch in enumerate(CH):
    m = cat[:, j] == 1
    print(ch, "mezzo", m.sum(), "t50 mediana %.0f, frazione fuori 380-480 ns: %.1f%%"
          % (np.median(t50[m, j]), 100 * np.mean((t50[m, j] < 380) | (t50[m, j] > 480))),
          "saturi t50 mediana %.0f" % np.median(t50[cat[:, j] == 2, j]))
