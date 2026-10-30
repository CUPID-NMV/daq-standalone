"""Sovrapposizione per PMT singolo: la carica di ciascun PMT in tutti gli eventi
col trigger, dati LNGS 0011 + 0013 contro il MC (gamma di Hall A divisi per
componente, piu' i muoni), dal piedistallo alle cariche alte.

    python3 analisi/20261029_normalizzazione_gamma/sovrapposizione_pmt_singolo.py OUT.png [GAMMA.npz:RESA]
            [--muoni MUONI.npz:RESA] [--v812 CARICHE.npz --evidenzia N ...]

Differenza da sovrapposizione_0011_0013.py: li' la selezione e' "entrambi i
PMT sopra 3 sigma" e si mostra il PMT piu' illuminato; qui nessuna selezione
oltre al trigger della run, e ogni evento entra due volte, una per PMT. Nel MC
il PMT senza luce porta il rumore delle tracce vere di piedistallo, quindi
anche il MC ha il suo picco a zero: e' la previsione di quante volte un PMT
resta buio quando l'altro fa scattare il trigger.
Asse x lineare attorno a zero e logaritmico oltre 2000 ADC*ns; tutto in Hz per
100 ADC*ns.
"""
# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))

import argparse
import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("gamma", nargs="?", default="mc_digit_gamma_righe_s3500.npz:3500")
ap.add_argument("--muoni", default="mc_digit_mu_lngs_s3500.npz:3500")
ap.add_argument("--v812", default=None)
ap.add_argument("--evidenzia", type=int, nargs="*", default=[])
arg = ap.parse_args()
GAMMA_F, RESA = (arg.gamma.split(":") + ["?"])[:2]
MU_F, RESA_MU = (arg.muoni.split(":") + ["?"])[:2]
MIS = _os.path.join(ROOT, "measurements")
SOGLIE = json.load(open(_os.path.join(MIS, "soglie_trigger_lngs.json")))
RUN = {"0011": "LNGS__54-0V_0011_750Ms_55PT", "0013": "LNGS__54-0V_0013_750Ms_55PT"}
T = {r: SOGLIE[n]["durata_s"] for r, n in RUN.items()}
TT = sum(T.values())


def apri(f):
    return np.load(f if _os.path.exists(f) else _os.path.join(MIS, f))


# dati: ogni evento registrato, i due PMT come due voci, peso 1/TT
dq, dch, dr = [], [], []
for r in RUN:
    z = np.load(_os.path.join(MIS, "carica_%s_testbeam.npz" % r))
    q = np.stack([z["q_caen_ch20"], z["q_caen_ch21"]], 1)
    dq.append(q.ravel())
    dch.append(np.tile([20, 21], len(q)))
    dr.append(np.full(q.size, r))
dq, dch, dr = np.concatenate(dq), np.concatenate(dch), np.concatenate(dr)
dw = np.full(len(dq), 1 / TT)

# MC: ogni evento col trigger della run, i due PMT come due voci, peso w0 * T_run / TT
mc = {}
for chiave, f in (("gamma", GAMMA_F), ("mu", MU_F)):
    z = apri(f)
    w0, rep = float(z["peso_hz"]), int(z["repliche"])
    qq, ww, rr, ee, cc = [], [], [], [], []
    for r in RUN:
        A = np.array(SOGLIE[RUN[r]]["soglia"])
        trig = (z["amp"][:, 0] >= A[0]) | (z["amp"][:, 1] >= A[1])
        qq.append(z["q"][trig].ravel())
        ww.append(np.full(2 * trig.sum(), w0 * T[r] / TT))
        rr.append(np.full(2 * trig.sum(), r))
        ee.append(np.repeat(np.nansum(z["edep_fibre"][trig], 1), 2))
        cc.append(np.tile([20, 21], trig.sum()))
    mc[chiave] = dict(q=np.concatenate(qq), w=np.concatenate(ww), r=np.concatenate(rr), ef=np.concatenate(ee),
                      ch=np.concatenate(cc), rep=rep, rate=float(z["rate_hz"]), ngen=int(z["n_gen"]), k=float(z["k"]))
K = mc["gamma"]["k"]

# rate sopra 3 sigma per PMT, dati e MC
riquadro = []
for ch, j in ((20, 0), (21, 1)):
    rd = sum(((dq[(dch == ch) & (dr == r)] > 3 * SOGLIE[RUN[r]]["sigma_q"][j]).sum()) for r in RUN) / TT
    rm = 0.0
    for m in mc.values():
        for r in RUN:
            s = (m["ch"] == ch) & (m["r"] == r)
            rm += m["w"][s][m["q"][s] > 3 * SOGLIE[RUN[r]]["sigma_q"][j]].sum()
    riquadro.append("ch%d above 3σ: data %.3f Hz, MC %.3f Hz (data/MC %.1f)" % (ch, rd, rm, rd / rm))
    print(riquadro[-1])
for r in RUN:
    nd = (dr == r).sum() / 2 / T[r]
    nm = sum(m["w"][(m["r"] == r)].sum() / 2 * TT / T[r] for m in mc.values())
    riquadro.append("run %s, trigger rate: data %.2f Hz, MC %.2f Hz" % (r, nd, nm))
    print(riquadro[-1])

b = np.concatenate([np.arange(-2000, 2000, 100.0), np.logspace(np.log10(2000), np.log10(200000), 21)])
dens = np.diff(b) / 100.0
bc = np.where(b[:-1] > 0, np.sqrt(np.clip(b[1:] * b[:-1], 1e-12, None)), 0.5 * (b[1:] + b[:-1]))


def n_indip(bins):
    # voci gamma col trigger della 0013 (contiene quelle della 0011), repliche correlate
    k = mc["gamma"]["r"] == "0013"
    return np.histogram(mc["gamma"]["q"][k], bins)[0] / mc["gamma"]["rep"]


def scala_x(a):
    a.set_xscale("symlog", linthresh=2000, linscale=0.6)
    a.set_xlim(-1500, 200000)


nrow = 3 if arg.v812 else 2
fig, ax = plt.subplots(nrow, 1, figsize=(13, 13.5 if arg.v812 else 10.5),
                       height_ratios=[2.2, 0.85, 1][:nrow] if arg.v812 else [2.2, 1], sharex=True)
a = ax[0]
hd = np.histogram(dq, b)[0]
ok = hd > 0
hg = np.histogram(mc["gamma"]["q"], b, weights=mc["gamma"]["w"])[0] / dens
hm = np.histogram(mc["mu"]["q"], b, weights=mc["mu"]["w"])[0] / dens
ni = n_indip(b)
et = (hg + hm) / np.sqrt(np.clip(ni, 1e-9, None)) * (ni > 0)
a.stairs(hg + hm + et, b, baseline=np.clip(hg + hm - et, 1e-14, None), fill=True, color="#7b2d8b", alpha=0.18, lw=0,
         label="MC statistical uncertainty (±1σ)")
a.stairs(hg + hm, b, color="#7b2d8b", lw=2.4, label="MC total, Hall A γ + μ")
sc = mc["gamma"]["ef"] > 0
for m_, col, nome in ((~sc, "#1aa39a", "Cherenkov light only (Compton electrons in the water)"),
                      (sc, "#e8710a", "with energy in the fibre cores (scintillation + Cherenkov)")):
    a.stairs(np.histogram(mc["gamma"]["q"][m_], b, weights=mc["gamma"]["w"][m_])[0] / dens, b, color=col, lw=1.4,
             ls="--", label="  Hall A γ, %s" % nome)
a.stairs(hm, b, color="#2a6fdb", lw=1.2, ls="--",
         label="  Hall A μ, fibre scintillation %s photons/MeV" % RESA_MU)
a.errorbar(bc[ok], hd[ok] / TT / dens[ok], yerr=np.sqrt(hd[ok]) / TT / dens[ok], xerr=[(bc - b[:-1])[ok], (b[1:] - bc)[ok]],
           fmt="o", ms=4, color="k", lw=0.8, zorder=5,
           label="DATA LNGS 0011 + 0013 (%.1f h at offset 200 + %.0f min at offset 100)" % (T["0011"] / 3600, T["0013"] / 60))
s_med = np.mean([np.mean(SOGLIE[RUN[r]]["sigma_q"]) for r in RUN])
a.axvline(3 * s_med, color="k", ls=":", lw=1.2, label="3σ of the pedestal, %.0f ADC·ns = %.2f PE" % (3 * s_med, 3 * s_med / K))
a.axvline(K, color="k", ls="-.", lw=1.0, label="1 photoelectron, K = %.0f ADC·ns" % K)
a.text(0.012, 0.025, "\n".join(riquadro), transform=a.transAxes, fontsize=8.5, va="bottom", ha="left",
       family="monospace", bbox=dict(facecolor="white", edgecolor="#bbb", alpha=0.92))
scala_x(a)
a.set_yscale("log")
a.set_ylim(1e-8, 30)
a.set_ylabel("rate per 100 ADC·ns [Hz]  (each PMT counted)")
a.set_title("charge of EACH PMT (ch20 and ch21 together) in all triggered events, no other selection; MC with the "
            "trigger of each run, time-weighted; K = %.0f ADC·ns/PE" % K, fontsize=9.5, loc="left")
a.legend(fontsize=8.5, frameon=False, loc="upper right")
a.grid(alpha=0.25, which="both")

if arg.v812:
    a = ax[1]
    z = apri(arg.v812)
    qv = np.stack([z["q_caen_ch20"], z["q_caen_ch21"]], 1)
    a.stairs(np.histogram(qv.ravel(), b)[0], b, fill=True, color="#8a8a8a", alpha=0.55, lw=0,
             label="all %d events, both PMTs (%d entries)" % (len(qv), qv.size))
    for e in arg.evidenzia:
        for j, ls in ((0, "-"), (1, "--")):
            a.axvline(qv[e, j], color="#d62728", lw=1.8, ls=ls,
                      label="event %d, ch%d: %.0f ADC·ns = %.1f PE" % (e, 20 + j, qv[e, j], qv[e, j] / K))
    a.set_ylabel("entries per bin")
    a.set_title("the WC with the V812 trigger: run %s, majority 4 of 5 with the three scintillator panels (counts)"
                % _os.path.basename(arg.v812).replace(".npz", "").replace("carica_", ""), fontsize=9.5, loc="left")
    a.legend(fontsize=8.5, frameon=False, loc="upper right")
    a.grid(alpha=0.25, which="both")
    scala_x(a)

a = ax[-1]
br = np.concatenate([np.arange(-2000, 2000, 400.0), np.logspace(np.log10(2000), np.log10(200000), 11)])
cr = np.where(br[:-1] > 0, np.sqrt(np.clip(br[1:] * br[:-1], 1e-12, None)), 0.5 * (br[1:] + br[:-1]))
nd = np.histogram(dq, br)[0]
sm = np.histogram(mc["gamma"]["q"], br, weights=mc["gamma"]["w"])[0] + np.histogram(mc["mu"]["q"], br, weights=mc["mu"]["w"])[0]
nn = n_indip(br)
okr = (nd > 0) & (nn > 0) & (sm > 0)
rr = (nd[okr] / TT) / sm[okr]
a.errorbar(cr[okr], rr, yerr=rr * np.sqrt(1 / nd[okr] + 1 / nn[okr]), xerr=[(cr - br[:-1])[okr], (br[1:] - cr)[okr]],
           fmt="o", ms=5, color="k", lw=1)
a.axhline(1, color="k", lw=0.8)
a.axvline(3 * s_med, color="k", ls=":", lw=1.2)
a.axvline(K, color="k", ls="-.", lw=1.0)
scala_x(a)
a.set_yscale("log")
a.set_ylim(0.3, 30)
a.set_xlabel("charge of a PMT [ADC·ns]  (gate 240-500 ns, pedestal 0-150 ns; 1 PE = %.0f ADC·ns)" % K)
a.set_ylabel("DATA / MC total")
a.set_title("data / MC (MC statistical error counts replicas as correlated)", fontsize=9.5, loc="left")
a.grid(alpha=0.25, which="both")
fig.suptitle("LNGS background, EACH PMT (runs 0011 + 0013) vs Monte Carlo: Hall A γ (J·A normalisation) + Hall A μ; "
             "scintillation of the fibre cores %s photons/MeV" % RESA, fontsize=11)
fig.tight_layout()
fig.savefig(arg.out, dpi=100)
print("scritto", arg.out)
