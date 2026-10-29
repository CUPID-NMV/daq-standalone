"""Differenza di tempo fra i due PMT contro asimmetria di carica, per i 36
eventi della run da 24 ore che non sono candidati muoni (pannelli deboli),
sopra il fondo della 0013 come riferimento.

    python3 analisi/20261029_geometria_pannelli/dt_asimmetria_24h.py F_24H F_0013 OUT.png

Stesso metodo di 20261030_fondo_0013/dt_pmt.py: tempo di ogni impulso al
passaggio al 50% del suo massimo, interpolato, nel cancello 240-500 ns;
dt = t(ch20) - t(ch21); cariche col piedistallo 0-150 ns e la sola
correzione CAEN. Il 3 sigma e' quello della 0013.

Attenzione nel leggerlo (CLAUDE.md, 29/10): col PMT debole a pochi PE il
passaggio al 50% cade sul primo fotone, tardi. La pendenza fra dt e
asimmetria viene in buona parte da li', non dalla posizione della luce.
Gira dove sono i dati (PC DAQ); scrive anche measurements/dt_asimmetria_24h.npz.
"""
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

F24, F13, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
MUONI = (17, 24)                   # candidati muoni della run da 24 ore (tre pannelli saturi)
DT = 4 / 3


def leggi(f):
    h, c, d, s = padme.leggi(f, [20, 21], None, serve_sic=False)
    t = np.arange(d.shape[2]) * DT
    y = -(d.astype(float) - d[:, :, t < 150].mean(2, keepdims=True))
    g = (t >= 240) & (t < 500)
    return t[g], y[:, :, g]


def t50(tg, w):
    """Primo passaggio al 50% del massimo, risalendo dal picco, interpolato."""
    p = int(w.argmax())
    soglia = 0.5 * w[p]
    i = p
    while i > 0 and w[i - 1] >= soglia:
        i -= 1
    if i == 0:
        return np.nan
    return tg[i - 1] + (soglia - w[i - 1]) / (w[i] - w[i - 1]) * DT


def sig(x):
    x = x[np.abs(x) < 1500]
    m, s_ = x.mean(), x.std()
    for _ in range(5):
        k = np.abs(x - m) < 3.5 * s_
        m, s_ = x[k].mean(), x[k].std()
    return s_


tg13, y13 = leggi(F13)
q13 = y13.sum(2) * DT
S = np.array([sig(q13[:, 0]), sig(q13[:, 1])])
b13 = np.where((q13[:, 0] > 3 * S[0]) & (q13[:, 1] > 3 * S[1]))[0]
T13 = np.array([[t50(tg13, y13[e, 0]), t50(tg13, y13[e, 1])] for e in b13])
dt13 = T13[:, 0] - T13[:, 1]
lr13 = np.log(q13[b13, 0] / q13[b13, 1])

tg24, y24 = leggi(F24)
q24 = y24.sum(2) * DT
T24 = np.array([[t50(tg24, y24[e, 0]), t50(tg24, y24[e, 1])] for e in range(len(q24))])
dt24 = T24[:, 0] - T24[:, 1]
both24 = (q24[:, 0] > 3 * S[0]) & (q24[:, 1] > 3 * S[1])
lr24 = np.log(np.clip(q24[:, 0], 1, None) / np.clip(q24[:, 1], 1, None))
altri = np.array([e not in MUONI for e in range(len(q24))])
m36 = altri & both24
np.savez(_os.path.join(ROOT, "measurements", "dt_asimmetria_24h.npz"), q24=q24, t24=T24, both24=both24,
         dt13=dt13, lr13=lr13, qmax13=q13[b13].max(1), sigma_q=S)

print("24 h: %d eventi, %d non candidati muoni, di cui %d con entrambi i PMT > 3 sigma" % (len(q24), altri.sum(), m36.sum()))
print("dt dei %d: mediana %.1f ns, rms %.1f ns, |dt| < 5 ns in %d" % (m36.sum(), np.median(dt24[m36]), dt24[m36].std(),
                                                                     (np.abs(dt24[m36]) < 5).sum()))
print("0013 (entrambi > 3 sigma, %d): mediana %.1f ns, rms %.1f ns" % (len(dt13), np.nanmedian(dt13), np.nanstd(dt13)))
r = np.corrcoef(lr24[m36], dt24[m36])[0, 1]
r13 = np.corrcoef(lr13[np.isfinite(dt13)], dt13[np.isfinite(dt13)])[0, 1]
print("correlazione dt - ln(Q20/Q21): 36 eventi %.2f, 0013 %.2f" % (r, r13))
for e in range(len(q24)):
    print("ev %2d  Q20 %6.0f  Q21 %6.0f  ln %+5.2f  t20 %6.1f  t21 %6.1f  dt %+6.1f %s"
          % (e, q24[e, 0], q24[e, 1], lr24[e], T24[e, 0], T24[e, 1], dt24[e],
             "candidato muone" if e in MUONI else ("" if both24[e] else "un PMT < 3 sigma")))

fig, ax = plt.subplots(1, 2, figsize=(18, 7), width_ratios=[1, 1.35])
a = ax[0]
# +-60 ns: fra 30 e 41 ns ci sono sei dei 34 eventi; oltre resta solo l'evento 37,
# col massimo di ch20 in fondo al cancello, dichiarato in legenda
LIM = 60.0
b = np.arange(-LIM, LIM + 1, 4.0)
fuori = m36 & (np.abs(dt24) > LIM)
h13 = np.histogram(dt13[np.isfinite(dt13)], b)[0]
a.stairs(h13 / h13.sum() * m36.sum(), b, color="#9a9a9a", lw=1.4,
         label="LNGS 0013 background, both PMTs > 3σ (%d events), scaled to %d" % (len(dt13), m36.sum()))
a.hist(dt24[m36], b, histtype="stepfilled", color="#2a6fdb", alpha=0.75,
       label="24-hour run: the %d non-muon events with both PMTs > 3σ%s"
             % (m36.sum(), "" if not fuori.any() else "\n(outside ±%.0f ns: %s)"
                % (LIM, ", ".join("event %d at %+.0f ns" % (e, dt24[e]) for e in np.where(fuori)[0]))))
a.axvline(dt24[17], color="#d62728", lw=2, label="event 17 (muon candidate): Δt = %.1f ns" % dt24[17])
a.set_xlabel("Δt = t(ch20) − t(ch21)  [ns]   (50% of each pulse's maximum)")
a.set_ylabel("events per 4 ns")
a.set_title("time difference between the two PMTs of the same event", fontsize=10, loc="left")
a.legend(fontsize=8.5, frameon=False, loc="upper left")
a.grid(alpha=0.25)

a = ax[1]
ok13 = np.isfinite(dt13)
a.scatter(lr13[ok13], dt13[ok13], s=5, color="#c4c4c4", lw=0, label="LNGS 0013 background, both PMTs > 3σ", zorder=1)
sc = a.scatter(lr24[m36], dt24[m36], s=55, c=np.log10(q24[m36].max(1)), cmap="viridis", edgecolor="k", lw=0.6,
               vmin=3.4, vmax=4.3, label="24-hour run, non-muon events (%d)" % m36.sum(), zorder=3)
plt.colorbar(sc, ax=a, label="log10 of the brighter PMT's charge [ADC·ns]")
uno = altri & ~both24
a.scatter(lr24[uno], dt24[uno], s=55, facecolor="none", edgecolor="#555", lw=1.2, zorder=3,
          label="one PMT below 3σ (events %s): its time is noise" % ", ".join(str(e) for e in np.where(uno)[0]))
a.scatter(lr24[17], dt24[17], s=260, marker="*", color="#d62728", edgecolor="k", lw=0.6, zorder=4,
          label="event 17, muon candidate")
# eventi oltre la scala: al bordo con una freccia e il valore
for k, e in enumerate(np.where(altri & (np.abs(dt24) > LIM))[0]):
    yb = np.sign(dt24[e]) * (LIM - 2)
    a.scatter(lr24[e], yb, s=70, marker="^" if dt24[e] > 0 else "v", color="#555", zorder=4)
    # etichette sfalsate in altezza, se no si sovrappongono
    a.annotate("event %d: Δt = %+.0f ns%s" % (e, dt24[e], "" if both24[e] else ", one PMT below 3σ"),
               (lr24[e], yb), textcoords="offset points", xytext=(8, -5 - 13 * k), fontsize=8.5, color="#333")
a.axhline(0, color="k", lw=0.6)
a.axvline(0, color="k", lw=0.6)
a.set_xlim(-4, 4)
a.set_ylim(-LIM, LIM)
a.set_xlabel("ln(Q20 / Q21)   (> 0: more light on ch20)")
a.set_ylabel("Δt = t(ch20) − t(ch21)  [ns]   (> 0: ch20 later)")
a.set_title("Δt vs charge asymmetry (weak pulses: the 50%% crossing comes late; correlation %.2f, background %.2f)"
            % (r, r13), fontsize=10, loc="left")
a.legend(fontsize=8.5, frameon=True, framealpha=0.92, edgecolor="none", loc="lower left")
a.grid(alpha=0.25)
fig.suptitle("LNGS 24-hour run (V812 majority 4 of 5 with the panels): time and charge sharing between the two PMTs "
             "of the WC", fontsize=11)
fig.tight_layout()
fig.savefig(OUT, dpi=100)
print("scritto", OUT)
