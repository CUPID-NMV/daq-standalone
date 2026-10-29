"""Fondo LNGS 0011 + 0013 sommato contro il MC dei gamma di Hall A con diverse
rese di scintillazione delle fibre (piu' i muoni, che non contano).

    python3 analisi/20261029_normalizzazione_gamma/confronto_rese.py OUT.png GAMMA1.npz:ETICHETTA [GAMMA2.npz:ETICHETTA ...]

GAMMA*.npz: uscite di digit_mc.py (per esempio measurements/mc_digit_gamma_righe_s3500.npz:3500).
Trigger e taglio a 3 sigma di ogni run, pesati con la durata, come in
sovrapposizione_0011_0013.py. Le fasce di carica dividono la classe C a
15000 ADC*ns: sopra, le due soglie sono tutte e due efficienti e le due run
danno lo stesso rate; fra 10000 e 15000 no.

Domanda: alzando la resa, l'eccesso ad alta carica si chiude senza rompere
l'accordo a carica bassa? E l'asimmetria fra i due PMT della classe C si
avvicina a quella dei dati?
"""
# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))

import json
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = sys.argv[1]
GAMMA = [(a.split(":")[0], a.split(":")[1] if ":" in a else _os.path.basename(a)) for a in sys.argv[2:]]
MIS = _os.path.join(ROOT, "measurements")
SOGLIE = json.load(open(_os.path.join(MIS, "soglie_trigger_lngs.json")))
RUN = {"0011": "LNGS__54-0V_0011_750Ms_55PT", "0013": "LNGS__54-0V_0013_750Ms_55PT"}
T = {r: SOGLIE[n]["durata_s"] for r, n in RUN.items()}
TT = sum(T.values())
FASCE = [("both > 3σ", None, None), ("< 7000", None, 7000), ("7000-10000", 7000, 10000),
         ("10000-15000", 10000, 15000), ("≥ 15000", 15000, None)]
COL = ["#7b2d8b", "#1aa39a", "#e8710a", "#2a6fdb"]


def scelti(q, amp, run):
    s = SOGLIE[RUN[run]]
    S, A = np.array(s["sigma_q"]), np.array(s["soglia"])
    trig = np.ones(len(q), bool) if amp is None else (amp[:, 0] >= A[0]) | (amp[:, 1] >= A[1])
    return trig & (q[:, 0] > 3 * S[0]) & (q[:, 1] > 3 * S[1])


def raccogli(z_q, z_amp, w0):
    """Eventi scelti in ciascuna run, uno per run, con peso w0 * T_run / TT (Hz)."""
    qq, ww, rr = [], [], []
    for r in RUN:
        m = scelti(z_q, z_amp, r)
        qq.append(z_q[m])
        ww.append(np.full(m.sum(), w0 * T[r] / TT))
        rr.append(np.full(m.sum(), r))
    return np.concatenate(qq), np.concatenate(ww), np.concatenate(rr)


def n_indip(m, bins):
    """Eventi Geant4 indipendenti per bin: i gamma scelti col trigger della 0013 (che
    contiene quelli della 0011: ogni evento vi entra una volta sola) diviso le repliche.
    I muoni non contano: hanno peso 1e-4 rispetto ai gamma."""
    k = m["gr"] == "0013"
    return np.histogram(m["gq"][k].max(1), bins)[0] / m["rep"]


def fasce(q2, w):
    qm = q2.max(1)
    out = {}
    for nome, lo, hi in FASCE:
        m = np.ones(len(qm), bool) if lo is None else qm >= lo
        if hi is not None:
            m &= qm < hi
        out[nome] = np.sum(w[m])
    return out


dq, dw = [], []
for r in RUN:
    z = np.load(_os.path.join(MIS, "carica_%s_testbeam.npz" % r))
    q = np.stack([z["q_caen_ch20"], z["q_caen_ch21"]], 1)
    m = scelti(q, None, r)
    dq.append(q[m])
    dw.append(np.full(m.sum(), 1 / TT))
dq, dw = np.concatenate(dq), np.concatenate(dw)
zmu = np.load(_os.path.join(MIS, "mc_digit_mu_lngs_s1750.npz"))
muq, muw, _ = raccogli(zmu["q"], zmu["amp"], float(zmu["peso_hz"]))
mc = []
for f, eti in GAMMA:
    z = np.load(f if _os.path.exists(f) else _os.path.join(MIS, f))
    gq, gw, gr = raccogli(z["q"], z["amp"], float(z["peso_hz"]))
    mc.append(dict(eti=eti, q=np.concatenate([gq, muq]), w=np.concatenate([gw, muw]), gq=gq, gr=gr,
                   rep=int(z["repliche"]), k=float(z["k"]), ngen=int(z["n_gen"])))

fd = fasce(dq, dw)
print("%-12s %10s" % ("", "dati") + "".join("%22s" % ("MC resa " + m["eti"]) for m in mc))
for nome, _, _ in FASCE:
    riga = "%-12s %8.3f Hz" % (nome, fd[nome])
    for m in mc:
        v = fasce(m["q"], m["w"])[nome]
        riga += "   %.3f (dati/MC %.2f)" % (v, fd[nome] / v)
    print(riga)


def asim(q2):
    return np.log(np.clip(q2[:, 0], 1, None) / np.clip(q2[:, 1], 1, None))


fig, ax = plt.subplots(2, 2, figsize=(20, 11), width_ratios=[1.45, 1], height_ratios=[2, 1])
# --- spettro
a = ax[0, 0]
b = np.logspace(np.log10(2000), np.log10(200000), 21)
bc = np.sqrt(b[1:] * b[:-1])
hd = np.histogram(dq.max(1), b)[0]
ok = hd > 0
for i, m in enumerate(mc):
    h = np.histogram(m["q"].max(1), b, weights=m["w"])[0]
    e = h / np.sqrt(np.clip(n_indip(m, b), 1e-9, None)) * (n_indip(m, b) > 0)
    a.stairs(h + e, b, baseline=np.clip(h - e, 1e-12, None), fill=True, color=COL[i], alpha=0.16, lw=0)
    a.stairs(h, b, color=COL[i], lw=2.2, label="MC Hall A γ + μ, fibre scintillation %s γ/MeV: %.3f Hz"
                                               % (m["eti"], fasce(m["q"], m["w"])["both > 3σ"]))
a.errorbar(bc[ok], hd[ok] / TT, yerr=np.sqrt(hd[ok]) / TT, xerr=[(bc - b[:-1])[ok], (b[1:] - bc)[ok]],
           fmt="o", ms=4, color="k", lw=0.8, zorder=5,
           label="DATA LNGS 0011 + 0013 (%.1f h): %.3f Hz" % (TT / 3600, fd["both > 3σ"]))
for v in (7000, 10000, 15000):
    a.axvline(v, color="#888", ls=":", lw=1)
a.set_xscale("log")
a.set_yscale("log")
a.set_ylim(1e-4, 0.3)
a.set_xlim(2000, 200000)
a.set_ylabel("rate per bin [Hz]  (10 bins per decade)")
a.set_title("both PMTs > 3σ, trigger and 3σ cut of each run, time-weighted; K = %.0f ADC·ns/PE (bands: MC stat. ±1σ, replicas correlated)"
            % mc[0]["k"], fontsize=9.5, loc="left")
a.legend(fontsize=9, frameon=False, loc="upper right")
a.grid(alpha=0.25, which="both")
K = mc[0]["k"]
sec = a.secondary_xaxis("top", functions=(lambda x: x / K, lambda x: x * K))
sec.set_xlabel("photoelectrons in the brighter PMT (charge / K)", fontsize=9)

# --- rapporto
a = ax[1, 0]
br = np.logspace(np.log10(2000), np.log10(200000), 11)
cr = np.sqrt(br[1:] * br[:-1])
nd = np.histogram(dq.max(1), br)[0]
for i, m in enumerate(mc):
    s_ = np.histogram(m["q"].max(1), br, weights=m["w"])[0]
    n_ = n_indip(m, br)
    ok = (nd > 0) & (n_ > 0)
    rr = (nd[ok] / TT) / s_[ok]
    err = rr * np.sqrt(1 / nd[ok] + 1 / n_[ok])
    a.errorbar(cr[ok] * (1 + 0.04 * (i - (len(mc) - 1) / 2)), rr, yerr=err, fmt="o", ms=5, color=COL[i], lw=1,
               label="DATA / MC (%s γ/MeV)" % m["eti"])
a.axhline(1, color="k", lw=0.8)
for v in (7000, 10000, 15000):
    a.axvline(v, color="#888", ls=":", lw=1)
a.set_xscale("log")
a.set_yscale("log")
a.set_ylim(0.2, 20)
a.set_xlim(2000, 200000)
a.set_xlabel("charge of the brighter PMT [ADC·ns]  (gate 240-500 ns, pedestal 0-150 ns)")
a.set_ylabel("DATA / MC")
a.legend(fontsize=9, frameon=False, loc="upper left")
a.grid(alpha=0.25, which="both")

# --- asimmetria della classe C
a = ax[0, 1]
bl = np.linspace(-5, 5, 41)
cC = dq.max(1) >= 10000
lr = asim(dq[cC])
a.hist(lr, bl, weights=dw[cC], density=True, histtype="step", color="k", lw=2,
       label="DATA: |ln| < 1 in %.0f%%, > 2 in %.0f%%"
             % (100 * np.average(np.abs(lr) < 1, weights=dw[cC]), 100 * np.average(np.abs(lr) > 2, weights=dw[cC])))
for i, m in enumerate(mc):
    c = m["q"].max(1) >= 10000
    l_ = asim(m["q"][c])
    a.hist(l_, bl, weights=m["w"][c], density=True, histtype="step", color=COL[i], lw=1.6,
           label="MC %s γ/MeV: |ln| < 1 in %.0f%%, > 2 in %.0f%%  (%d γ entries, %d independent)"
                 % (m["eti"], 100 * np.average(np.abs(l_) < 1, weights=m["w"][c]),
                    100 * np.average(np.abs(l_) > 2, weights=m["w"][c]),
                    (m["gq"][m["gr"] == "0013"].max(1) >= 10000).sum(),
                    (m["gq"][m["gr"] == "0013"].max(1) >= 10000).sum() // m["rep"]))
a.set_xlabel("ln(Q20 / Q21)")
a.set_ylabel("normalised")
a.set_title("class C (brighter PMT ≥ 10000 ADC·ns): light sharing between the two PMTs", fontsize=9.5, loc="left")
a.legend(fontsize=8.5, frameon=False, loc="upper left")
a.grid(alpha=0.25)

# --- rate per fascia
a = ax[1, 1]
nomi = [f[0] for f in FASCE]
x = np.arange(len(nomi))
serie = [("DATA 0011 + 0013", fd, "k")] + [("MC %s γ/MeV" % m["eti"], fasce(m["q"], m["w"]), COL[i])
                                           for i, m in enumerate(mc)]
wb = 0.8 / len(serie)
for i, (lab, v, col) in enumerate(serie):
    vv = np.array([v[n] for n in nomi])
    xx = x + (i - (len(serie) - 1) / 2) * wb
    a.bar(xx, vv, wb, color=col, label=lab)
    for xi, vi in zip(xx, vv):
        a.text(xi, vi * 1.15, "%.3f" % vi, ha="center", fontsize=7, rotation=90)
a.set_yscale("log")
a.set_ylim(5e-3, 3)
a.set_xticks(x)
a.set_xticklabels(nomi, fontsize=9)
a.set_ylabel("rate [Hz]")
a.set_title("rates per charge band [ADC·ns]", fontsize=9.5, loc="left")
a.legend(fontsize=8.5, frameon=False, loc="upper right")
a.grid(alpha=0.25, axis="y", which="both")
fig.suptitle("LNGS background (0011 + 0013) vs Hall A γ + μ in the MC with different scintillation yields "
             "of the fibre cores (J·A normalisation)", fontsize=11)
fig.tight_layout()
fig.savefig(OUT, dpi=95)
print("scritto", OUT)
