"""Confronto SiPM 51 V (sotto breakdown) contro 54 V, senza PADME.
Piedistallo 0-150 ns, cancello 200 ns -> fine traccia."""
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

f51, f54, pref = sys.argv[1], sys.argv[2], sys.argv[3]
CH = [16, 17, 18]
dt = 4 / 3
RUN = {"51 V": (f51, "#d62728"), "54 V (0011)": (f54, "#2a6fdb")}
L = np.array([25, 50, 75, 100, 150, 200, 300, 400, 600, 800, 1000, 1148])

dati = {}
for nome, (f, _) in RUN.items():
    h, c, d, s = padme.leggi(f, CH, None)
    dati[nome] = d.astype(float)
ns = dati["51 V"].shape[2]
t = np.arange(ns) * dt
p = t < 150
g = t >= 200
i0 = int(np.searchsorted(t, 200))

# ---- Figura A: livello del piedistallo, carica con e senza sottrazione
figA, axA = plt.subplots(3, 3, figsize=(17, 11))
# ---- Figura B: sigma contro lunghezza della finestra, traccia media
figB, axB = plt.subplots(3, 2, figsize=(16, 11), width_ratios=[1, 1.4])
for r, ch in enumerate(CH):
    for nome, (f, col) in RUN.items():
        y = dati[nome][:, r, :]
        n = len(y)
        w = np.full(n, 1.0 / n)
        ped = y[:, p].mean(1)
        raw = y[:, g].sum(1) * dt
        sub = (y[:, g] - ped[:, None]).sum(1) * dt
        lv = ped - np.median(ped)
        a = axA[r, 0]
        a.hist(lv, np.linspace(-6, 30, 145), weights=w, histtype="step", color=col, lw=1.4,
               label="%s, %d ev.: rms %.2f, robust σ %.2f counts" % (nome, n, lv.std(), padme.sigma_robusta(lv)))
        a = axA[r, 1]
        a.hist(sub - np.median(sub), np.linspace(-30000, 40000, 141), weights=w, histtype="step",
               color=col, lw=1.4, label="%s: σ_core %.0f" % (nome, padme.sigma_robusta(sub)))
        a = axA[r, 2]
        a.hist(raw - np.median(raw), np.linspace(-30000, 40000, 141), weights=w, histtype="step",
               color=col, lw=1.4, label="%s: σ_core %.0f" % (nome, padme.sigma_robusta(raw)))

        z = y - ped[:, None]
        quieti = np.abs(z[:, g]).max(1) < 15
        sig = [padme.sigma_robusta(z[quieti, i0:i0 + int(round(l / dt))].sum(1) * dt) for l in L]
        axB[r, 0].plot(L, sig, "o-", color=col, label="%s (%d of %d events quiet)" % (nome, quieti.sum(), n))
        axB[r, 1].plot(t, z.mean(0), color=col, lw=0.9, label="%s, all events" % nome)
        print(ch, nome, "eventi", n, "rms livello %.2f robusta %.2f" % (lv.std(), padme.sigma_robusta(lv)),
              "sigma sub %.0f raw %.0f" % (padme.sigma_robusta(sub), padme.sigma_robusta(raw)),
              "quieti %d" % quieti.sum(), "sigma(1150) %.0f" % sig[-1],
              "livello assoluto %.1f" % np.median(y[:, p].mean(1)))

    for a, tit, xl in ((axA[r, 0], "baseline 0-150 ns, event by event (− median)", "[counts]"),
                       (axA[r, 1], "charge 200-end, baseline 0-150 SUBTRACTED (− median)", "[ADC·ns]"),
                       (axA[r, 2], "charge 200-end, NO subtraction (− median)", "[ADC·ns]")):
        a.set_yscale("log")
        a.set_title("ch%d  %s" % (ch, tit), fontsize=9.5, loc="left")
        a.set_xlabel(xl)
        a.set_ylabel("fraction of events / bin")
        a.legend(fontsize=7.5, frameon=False)
        a.grid(alpha=0.25, which="both")
    a = axB[r, 0]
    a.set_xlabel("integration window from 200 ns, length [ns]")
    a.set_ylabel("σ of blank charge [ADC·ns]")
    a.set_title("ch%d  noise on the charge vs window length (baseline 0-150 subtracted)" % ch,
                fontsize=9.5, loc="left")
    a.legend(fontsize=8)
    a.grid(alpha=0.25)
    a = axB[r, 1]
    a.axvspan(0, 150, color="#888", alpha=0.2)
    a.axhline(0, color="#888", lw=0.7)
    a.set_ylim(-3, 6)
    a.set_xlabel("time [ns]")
    a.set_ylabel("[counts]")
    a.set_title("ch%d  mean trace, baseline 0-150 subtracted" % ch, fontsize=9.5, loc="left")
    a.legend(fontsize=8)
    a.grid(alpha=0.25)
figA.suptitle("SiPM 51 V (below breakdown, no dark counts) vs 54 V — CAEN correction only, no PADME; "
              "histograms normalised to the number of events", fontsize=11)
figA.tight_layout()
figA.savefig(pref + "_A.png", dpi=95)
figB.suptitle("SiPM 51 V vs 54 V — noise vs window length and mean trace", fontsize=11)
figB.tight_layout()
figB.savefig(pref + "_B.png", dpi=95)

# ---- Figura C: 20 forme d'onda a 51 V, i tre SiPM
y = dati["51 V"]
fig, ax = plt.subplots(5, 4, figsize=(17, 13), sharex=True, sharey=True)
cc = {16: "#2a6fdb", 17: "#1aa39a", 18: "#8a5cd6"}
for a, e in zip(ax.ravel(), range(20)):
    for r, ch in enumerate(CH):
        z = y[e, r] - y[e, r, p].mean()
        a.plot(t, z + 15 * r, lw=0.6, color=cc[ch], label="ch%d" % ch if e == 0 else None)
    a.axvspan(0, 150, color="#888", alpha=0.15)
    a.set_title("51 V, event %d" % e, fontsize=8.5)
    a.grid(alpha=0.25)
ax[0, 0].legend(fontsize=7, loc="upper right")
for a in ax[-1]:
    a.set_xlabel("time [ns]")
for a in ax[:, 0]:
    a.set_ylabel("ADC − baseline 0-150 [counts]\n(ch17 +15, ch18 +30)")
fig.suptitle("SiPM at 51 V: first 20 events, CAEN correction only — grey: baseline window 0-150 ns",
             fontsize=11)
fig.tight_layout()
fig.savefig(pref + "_C.png", dpi=95)
