"""Fondo LNGS misurato, 0011 + 0013 sommate, contro il MC digitalizzato:
gamma di Hall A (Compton + scintillazione delle fibre) e muoni di Hall A.

    python3 analisi/20261029_normalizzazione_gamma/sovrapposizione_0011_0013.py OUT.png [GAMMA.npz:RESA]

GAMMA.npz: i gamma digitalizzati da usare (di serie mc_digit_gamma_righe_s1750.npz:1750);
RESA e' la resa di scintillazione delle fibre di quella produzione, per la legenda.

Le due run hanno soglie diverse (offset 200 e 100): sotto ~10000 ADC*ns la
somma mescola due trigger. Il MC si somma allo stesso modo: per ogni run il
suo trigger (bordo in ampiezza misurato da soglie_lngs.py) e il suo taglio a
3 sigma, pesati con la durata della run. Il digitalizzatore salva l'ampiezza
di ogni evento con almeno un PE, quindi il trigger si riapplica senza
ridigitalizzare.

Selezione: entrambi i PMT sopra 3 sigma; si mostra la carica del PMT piu'
illuminato (cancello 240-500 ns, piedistallo 0-150 ns, correzione CAEN).
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
GAMMA_F, RESA = (sys.argv[2].split(":") + ["?"])[:2] if len(sys.argv) > 2 else ("mc_digit_gamma_righe_s1750.npz", "1750")
MIS = _os.path.join(ROOT, "measurements")
SOGLIE = json.load(open(_os.path.join(MIS, "soglie_trigger_lngs.json")))
RUN = {"0011": "LNGS__54-0V_0011_750Ms_55PT", "0013": "LNGS__54-0V_0013_750Ms_55PT"}
T = {r: SOGLIE[n]["durata_s"] for r, n in RUN.items()}
TT = sum(T.values())
CLASSI = [("both > 3σ", None, None), ("B: < 7000", None, 7000), ("7000-10000", 7000, 10000),
          ("C: ≥ 10000", 10000, None)]


def scelti(q, amp, run):
    s = SOGLIE[RUN[run]]
    S, A = np.array(s["sigma_q"]), np.array(s["soglia"])
    trig = np.ones(len(q), bool) if amp is None else (amp[:, 0] >= A[0]) | (amp[:, 1] >= A[1])
    return trig & (q[:, 0] > 3 * S[0]) & (q[:, 1] > 3 * S[1])


def classi(qm, w):
    out = {}
    for nome, lo, hi in CLASSI:
        m = np.ones(len(qm), bool) if lo is None else qm >= lo
        if hi is not None:
            m &= qm < hi
        out[nome] = np.sum(w[m])
    return out


# dati: massimo dei due PMT degli eventi scelti, con peso 1/TT (Hz sulla durata totale)
dq, dw, dr = [], [], []
for r in RUN:
    z = np.load(_os.path.join(MIS, "carica_%s_testbeam.npz" % r))
    q = np.stack([z["q_caen_ch20"], z["q_caen_ch21"]], 1)
    m = scelti(q, None, r)
    dq.append(q[m].max(1))
    dw.append(np.full(m.sum(), 1 / TT))
    dr.append(np.full(m.sum(), r))
dq, dw, dr = np.concatenate(dq), np.concatenate(dw), np.concatenate(dr)

# MC: ogni evento entra una volta per run, col trigger e il taglio di quella run, pesato T_run / TT
mc = {}
for chiave, f in (("gamma", GAMMA_F), ("mu", "mc_digit_mu_lngs_s1750.npz")):
    z = np.load(f if _os.path.exists(f) else _os.path.join(MIS, f))
    w0, rep = float(z["peso_hz"]), int(z["repliche"])
    qq, ww, rr = [], [], []
    for r in RUN:
        m = scelti(z["q"], z["amp"], r)
        qq.append(z["q"][m].max(1))
        ww.append(np.full(m.sum(), w0 * T[r] / TT))
        rr.append(np.full(m.sum(), r))
    mc[chiave] = dict(q=np.concatenate(qq), w=np.concatenate(ww), r=np.concatenate(rr), rep=rep,
                      rate=float(z["rate_hz"]), ngen=int(z["n_gen"]), k=float(z["k"]))

cd = classi(dq, dw)
cg, cm = classi(mc["gamma"]["q"], mc["gamma"]["w"]), classi(mc["mu"]["q"], mc["mu"]["w"])
print("durate: 0011 %.0f s, 0013 %.0f s; eventi scelti nei dati: 0011 %d, 0013 %d"
      % (T["0011"], T["0013"], (dr == "0011").sum(), (dr == "0013").sum()))
for nome, _, _ in CLASSI:
    print("%-12s dati %.3f Hz | MC gamma %.3f + muoni %.5f = %.3f | dati/MC %.2f"
          % (nome, cd[nome], cg[nome], cm[nome], cg[nome] + cm[nome], cd[nome] / (cg[nome] + cm[nome])))

K = mc["gamma"]["k"]
fig, ax = plt.subplots(2, 1, figsize=(13, 10.5), height_ratios=[2.2, 1], sharex=True)
a = ax[0]
# asse logaritmico: la coda arriva a ~170000 ADC*ns (l'11% degli eventi della 0011 sta sopra
# 40000) e la scala lineare la tagliava. Dieci bin per decade.
b = np.logspace(np.log10(2000), np.log10(200000), 21)
bc = np.sqrt(b[1:] * b[:-1])
hd = np.histogram(dq, b)[0]
ok = hd > 0
hg = np.histogram(mc["gamma"]["q"], b, weights=mc["gamma"]["w"])[0]
hm = np.histogram(mc["mu"]["q"], b, weights=mc["mu"]["w"])[0]
# errore statistico del MC dagli eventi Geant4 indipendenti: i gamma scelti col trigger
# della 0013 (contiene quelli della 0011, ogni evento vi entra una volta) diviso le
# repliche, che contano come correlate. I muoni hanno peso 1e-4 e non contano.
def n_indip(bins):
    k = mc["gamma"]["r"] == "0013"
    return np.histogram(mc["gamma"]["q"][k], bins)[0] / mc["gamma"]["rep"]


ni = n_indip(b)
et = (hg + hm) / np.sqrt(np.clip(ni, 1e-9, None)) * (ni > 0)
a.stairs(hg + hm + et, b, baseline=np.clip(hg + hm - et, 1e-12, None), fill=True, color="#7b2d8b", alpha=0.18,
         lw=0, label="MC statistical uncertainty (±1σ)")
a.stairs(hg + hm, b, color="#7b2d8b", lw=2.4,
         label="MC total, Hall A γ + μ: %.3f Hz" % (cg["both > 3σ"] + cm["both > 3σ"]))
a.stairs(hg, b, color="#e8710a", lw=1.2, ls="--",
         label="  Hall A γ, Compton + fibre scintillation %s γ/MeV (%.0f Hz generated, %d M): %.3f Hz"
               % (RESA, mc["gamma"]["rate"], mc["gamma"]["ngen"] // 10**6, cg["both > 3σ"]))
a.stairs(hm, b, color="#2a6fdb", lw=1.2, ls="--",
         label="  Hall A μ, fibre scintillation 1750 γ/MeV (%.4f Hz on 6.5 × 6.5 m, %d k): %.5f Hz"
               % (mc["mu"]["rate"], mc["mu"]["ngen"] // 1000, cm["both > 3σ"]))
a.errorbar(bc[ok], hd[ok] / TT, yerr=np.sqrt(hd[ok]) / TT, xerr=[(bc - b[:-1])[ok], (b[1:] - bc)[ok]],
           fmt="o", ms=4, color="k", lw=0.8, zorder=5,
           label="DATA LNGS 0011 + 0013 (%.1f h at offset 200 + %.0f min at offset 100): %.3f Hz"
                 % (T["0011"] / 3600, T["0013"] / 60, cd["both > 3σ"]))
for v in (7000, 10000):
    a.axvline(v, color="#888", ls=":", lw=1)
a.text(10400, 1.3e-7, "class C →", fontsize=9, color="#555")
a.set_xscale("log")
a.set_yscale("log")
a.set_ylim(1e-7, 3)   # spazio per la legenda sopra i dati
a.set_ylabel("rate per bin [Hz]  (10 bins per decade)")
a.set_title("both PMTs > 3σ; MC with the trigger and the 3σ cut of each run (edges %.0f/%.0f and %.0f/%.0f counts), "
            "time-weighted;  K = %.0f ADC·ns/PE"
            % tuple(SOGLIE[RUN["0011"]]["soglia"] + SOGLIE[RUN["0013"]]["soglia"] + [K]),
            fontsize=9.5, loc="left")
a.legend(fontsize=9, frameon=False, loc="upper right")
a.grid(alpha=0.25, which="both")
sec = a.secondary_xaxis("top", functions=(lambda x: x / K, lambda x: x * K))
sec.set_xlabel("photoelectrons in the brighter PMT (charge / K)", fontsize=9)

a = ax[1]
br = np.logspace(np.log10(2000), np.log10(200000), 11)
cr = np.sqrt(br[1:] * br[:-1])
for chi, col, al, mk, lab in ((None, "k", 1.0, "o", "0011 + 0013"), ("0011", "#1aa39a", 0.6, "s", "0011 alone"),
                              ("0013", "#d62728", 0.6, "^", "0013 alone")):
    md = np.ones(len(dq), bool) if chi is None else dr == chi
    mg = np.ones(len(mc["gamma"]["q"]), bool) if chi is None else mc["gamma"]["r"] == chi
    mmu = np.ones(len(mc["mu"]["q"]), bool) if chi is None else mc["mu"]["r"] == chi
    tt = TT if chi is None else T[chi]
    nd = np.histogram(dq[md], br)[0]
    # MC riportato in Hz per la durata di quel gruppo di run
    fg = TT / tt
    sg = np.histogram(mc["gamma"]["q"][mg], br, weights=mc["gamma"]["w"][mg] * fg)[0]
    sm = np.histogram(mc["mu"]["q"][mmu], br, weights=mc["mu"]["w"][mmu] * fg)[0]
    # eventi indipendenti: per la somma quelli della 0013 (un evento conta una volta)
    ng = n_indip(br) if chi is None else np.histogram(mc["gamma"]["q"][mg], br)[0] / mc["gamma"]["rep"]
    s_ = sg + sm
    ok = (nd > 0) & (ng > 0)
    rr = (nd[ok] / tt) / s_[ok]
    err = rr * np.sqrt(1 / nd[ok] + 1 / ng[ok])
    sp = 1.0 if chi is None else (0.95 if chi == "0011" else 1.05)
    a.errorbar(cr[ok] * sp, rr, yerr=err, xerr=None if chi else [(cr - br[:-1])[ok], (br[1:] - cr)[ok]], fmt=mk,
               color=col, ms=5 if chi is None else 4, lw=1 if chi is None else 0.8, alpha=al, label=lab)
a.axhline(1, color="k", lw=0.8)
for v in (7000, 10000):
    a.axvline(v, color="#888", ls=":", lw=1)
a.set_xscale("log")
a.set_yscale("log")
a.set_ylim(0.3, 30)
a.set_xlim(2000, 200000)
a.set_xlabel("charge of the brighter PMT [ADC·ns]  (gate 240-500 ns, pedestal 0-150 ns)")
a.set_ylabel("DATA / MC total")
a.set_title("data / MC (MC statistical error counts replicas as correlated)", fontsize=9.5, loc="left")
a.legend(fontsize=8.5, frameon=False, loc="upper left", ncol=3)
a.grid(alpha=0.25, which="both")
fig.suptitle("LNGS background (runs 0011 + 0013) vs Monte Carlo: Hall A γ (J·A normalisation, φ = 1.12 cm$^{-2}$s$^{-1}$) "
             "+ Hall A μ, fibre scintillation %s γ/MeV" % RESA, fontsize=11)
fig.tight_layout()
fig.savefig(OUT, dpi=100)
print("scritto", OUT)
