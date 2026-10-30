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

Con --un-pmt-scan SCAN.npz (l'uscita di 20261030_calibrazione_selftrigger/
un_pmt_scan.py) il modello diventa ibrido: dal MC si tolgono gli eventi con un
solo PMT sopra 3 sigma, e al loro posto si mettono quelli misurati nello scan
del self-trigger del 2026-10-30 al passo con lo stesso offset della run (100
per la 0013, 200 per la 0011), con le loro due cariche. Toglierli dal MC evita
di contare due volte la luce che arriva a un PMT solo, che il MC ha e lo scan
anche. Le cariche dello scan sono state prese con la stessa ricetta, ma con i
PMT invertiti e un piedistallo piu' largo (sigma 320/370 contro 240-270).
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
ap.add_argument("--un-pmt-scan", default=None)
arg = ap.parse_args()
GAMMA_F, RESA = (arg.gamma.split(":") + ["?"])[:2]
MU_F, RESA_MU = (arg.muoni.split(":") + ["?"])[:2]
MIS = _os.path.join(ROOT, "measurements")
SOGLIE = json.load(open(_os.path.join(MIS, "soglie_trigger_lngs.json")))
RUN = {"0011": "LNGS__54-0V_0011_750Ms_55PT", "0013": "LNGS__54-0V_0013_750Ms_55PT"}
T = {r: SOGLIE[n]["durata_s"] for r, n in RUN.items()}
TT = sum(T.values())
OFFSET = {"0011": 200, "0013": 100}


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
    qq, ww, rr, ee, cc, uu = [], [], [], [], [], []
    for r in RUN:
        A = np.array(SOGLIE[RUN[r]]["soglia"])
        S = np.array(SOGLIE[RUN[r]]["sigma_q"])
        trig = (z["amp"][:, 0] >= A[0]) | (z["amp"][:, 1] >= A[1])
        sopra = z["q"] > 3 * S[None, :]
        uu.append(np.repeat((sopra[:, 0] ^ sopra[:, 1])[trig], 2))
        qq.append(z["q"][trig].ravel())
        ww.append(np.full(2 * trig.sum(), w0 * T[r] / TT))
        rr.append(np.full(2 * trig.sum(), r))
        ee.append(np.repeat(np.nansum(z["edep_fibre"][trig], 1), 2))
        cc.append(np.tile([20, 21], trig.sum()))
    mc[chiave] = dict(q=np.concatenate(qq), w=np.concatenate(ww), r=np.concatenate(rr), ef=np.concatenate(ee),
                      ch=np.concatenate(cc), u=np.concatenate(uu), rep=rep, rate=float(z["rate_hz"]),
                      ngen=int(z["n_gen"]), k=float(z["k"]))
K = mc["gamma"]["k"]

# Lo scan: eventi con un PMT solo al passo con l'offset della run, le due
# cariche come due voci, peso (1 / durata del passo) * T_run / TT.
sc_q = sc_w = sc_r = sc_ch = None
if arg.un_pmt_scan:
    z = apri(arg.un_pmt_scan)
    durate = dict(zip(z["passo"].tolist(), z["durata"].tolist()))
    Ss = z["sigma_q"]
    qq, ww, rr, cc = [], [], [], []
    for r in RUN:
        for j, ch in ((0, 20), (1, 21)):
            k = (z["canale"] == ch) & (z["offset"] == OFFSET[r])
            q = z["q"][k]
            solo = (q[:, j] > 3 * Ss[j]) & (q[:, 1 - j] <= 3 * Ss[1 - j])
            qq.append(q[solo].ravel())
            ww.append(np.full(2 * solo.sum(), T[r] / TT / durate["ch%d_off%g" % (ch, OFFSET[r])]))
            rr.append(np.full(2 * solo.sum(), r))
            cc.append(np.tile([20, 21], solo.sum()))
    sc_q, sc_w, sc_r, sc_ch = (np.concatenate(x) for x in (qq, ww, rr, cc))

# rate sopra 3 sigma per PMT, dati e MC
riquadro = []
for ch, j in ((20, 0), (21, 1)):
    rd = sum(((dq[(dch == ch) & (dr == r)] > 3 * SOGLIE[RUN[r]]["sigma_q"][j]).sum()) for r in RUN) / TT
    rm = 0.0
    for m in mc.values():
        for r in RUN:
            s = (m["ch"] == ch) & (m["r"] == r)
            rm += m["w"][s][m["q"][s] > 3 * SOGLIE[RUN[r]]["sigma_q"][j]].sum()
    if sc_q is None:
        riquadro.append("ch%d above 3σ: data %.3f Hz, MC %.3f Hz (data/MC %.1f)" % (ch, rd, rm, rd / rm))
    else:
        ri = 0.0
        for m in mc.values():
            for r in RUN:
                s_ = (m["ch"] == ch) & (m["r"] == r) & ~m["u"]
                ri += m["w"][s_][m["q"][s_] > 3 * SOGLIE[RUN[r]]["sigma_q"][j]].sum()
        Ss = apri(arg.un_pmt_scan)["sigma_q"]
        ri += sc_w[(sc_ch == ch) & (sc_q > 3 * Ss[j])].sum()
        riquadro.append("ch%d above 3σ: data %.3f Hz, MC %.3f Hz, hybrid %.3f Hz (data/hybrid %.2f)"
                        % (ch, rd, rm, ri, rd / ri))
    print(riquadro[-1])
for r in RUN:
    nd = (dr == r).sum() / 2 / T[r]
    nm = sum(m["w"][(m["r"] == r)].sum() / 2 * TT / T[r] for m in mc.values())
    if sc_q is None:
        riquadro.append("run %s, trigger rate: data %.2f Hz, MC %.2f Hz" % (r, nd, nm))
    else:
        ni_ = sum(m["w"][(m["r"] == r) & ~m["u"]].sum() / 2 * TT / T[r] for m in mc.values())
        ni_ += sc_w[sc_r == r].sum() / 2 * TT / T[r]
        riquadro.append("run %s, trigger rate: data %.2f Hz, MC %.2f Hz, hybrid %.2f Hz" % (r, nd, nm, ni_))
    print(riquadro[-1])

b = np.concatenate([np.arange(-2000, 2000, 100.0), np.logspace(np.log10(2000), np.log10(200000), 21)])
dens = np.diff(b) / 100.0
bc = np.where(b[:-1] > 0, np.sqrt(np.clip(b[1:] * b[:-1], 1e-12, None)), 0.5 * (b[1:] + b[:-1]))


def n_indip(bins, tenuti=None):
    # voci gamma col trigger della 0013 (contiene quelle della 0011), repliche correlate
    k = mc["gamma"]["r"] == "0013"
    if tenuti is not None:
        k = k & tenuti
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
if sc_q is None:
    ni = n_indip(b)
    et = (hg + hm) / np.sqrt(np.clip(ni, 1e-9, None)) * (ni > 0)
    a.stairs(hg + hm + et, b, baseline=np.clip(hg + hm - et, 1e-14, None), fill=True, color="#7b2d8b", alpha=0.18,
             lw=0, label="MC statistical uncertainty (±1σ)")
    a.stairs(hg + hm, b, color="#7b2d8b", lw=2.4, label="MC total, Hall A γ + μ")
    sc = mc["gamma"]["ef"] > 0
    for m_, col, nome in ((~sc, "#1aa39a", "Cherenkov light only (Compton electrons in the water)"),
                          (sc, "#e8710a", "with energy in the fibre cores (scintillation + Cherenkov)")):
        a.stairs(np.histogram(mc["gamma"]["q"][m_], b, weights=mc["gamma"]["w"][m_])[0] / dens, b, color=col,
                 lw=1.4, ls="--", label="  Hall A γ, %s" % nome)
    a.stairs(hm, b, color="#2a6fdb", lw=1.2, ls="--",
             label="  Hall A μ, fibre scintillation %s photons/MeV" % RESA_MU)
else:
    kg, km = ~mc["gamma"]["u"], ~mc["mu"]["u"]
    hgk = np.histogram(mc["gamma"]["q"][kg], b, weights=mc["gamma"]["w"][kg])[0] / dens
    hmk = np.histogram(mc["mu"]["q"][km], b, weights=mc["mu"]["w"][km])[0] / dens
    hs = np.histogram(sc_q, b, weights=sc_w)[0] / dens
    hs2 = np.histogram(sc_q, b, weights=sc_w ** 2)[0] / dens ** 2
    nik = n_indip(b, kg)
    emc2 = np.where(nik > 0, (hgk + hmk) ** 2 / np.clip(nik, 1e-9, None), 0.0)
    ibr = hgk + hmk + hs
    et = np.sqrt(emc2 + hs2)
    a.stairs(ibr + et, b, baseline=np.clip(ibr - et, 1e-14, None), fill=True, color="#c0392b", alpha=0.16, lw=0,
             label="hybrid statistical uncertainty (±1σ: MC replicas and scan events)")
    a.stairs(hg + hm, b, color="#7b2d8b", lw=1.2, alpha=0.75, label="MC total, Hall A γ + μ (as before)")
    a.stairs(hgk + hmk, b, color="#7b2d8b", lw=1.4, ls="--",
             label="  MC without its events with ONE PMT above 3σ")
    a.stairs(hs, b, color="#e8710a", lw=1.6, ls="--",
             label="  events with ONE PMT above 3σ measured in the self-trigger scan of 30/10, at the offset of each run")
    a.stairs(ibr, b, color="#c0392b", lw=2.6, label="HYBRID = MC without its one-PMT events + one-PMT events of the scan")
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
if sc_q is None:
    a.errorbar(cr[okr], rr, yerr=rr * np.sqrt(1 / nd[okr] + 1 / nn[okr]), xerr=[(cr - br[:-1])[okr], (br[1:] - cr)[okr]],
               fmt="o", ms=5, color="k", lw=1)
else:
    a.errorbar(cr[okr] * 0.97, rr, yerr=rr * np.sqrt(1 / nd[okr] + 1 / nn[okr]), fmt="o", ms=4, color="#9a9a9a",
               lw=0.8, label="data / MC (as before)")
    kg, km = ~mc["gamma"]["u"], ~mc["mu"]["u"]
    smk = (np.histogram(mc["gamma"]["q"][kg], br, weights=mc["gamma"]["w"][kg])[0]
           + np.histogram(mc["mu"]["q"][km], br, weights=mc["mu"]["w"][km])[0])
    ss = np.histogram(sc_q, br, weights=sc_w)[0]
    ss2 = np.histogram(sc_q, br, weights=sc_w ** 2)[0]
    nnk = n_indip(br, kg)
    mod = smk + ss
    var = np.where(nnk > 0, smk ** 2 / np.clip(nnk, 1e-9, None), 0.0) + ss2
    oki = (nd > 0) & (mod > 0)
    ri = (nd[oki] / TT) / mod[oki]
    a.errorbar(cr[oki] * 1.03, ri, yerr=ri * np.sqrt(1 / nd[oki] + var[oki] / mod[oki] ** 2),
               xerr=[(cr - br[:-1])[oki], (br[1:] - cr)[oki]], fmt="o", ms=5, color="#c0392b", lw=1,
               label="data / HYBRID")
    a.legend(fontsize=8.5, frameon=False, loc="upper left")
a.axhline(1, color="k", lw=0.8)
a.axvline(3 * s_med, color="k", ls=":", lw=1.2)
a.axvline(K, color="k", ls="-.", lw=1.0)
scala_x(a)
a.set_yscale("log")
a.set_ylim(0.3, 30)
a.set_xlabel("charge of a PMT [ADC·ns]  (gate 240-500 ns, pedestal 0-150 ns; 1 PE = %.0f ADC·ns)" % K)
a.set_ylabel("DATA / MC total" if sc_q is None else "DATA / model")
a.set_title("data / MC (MC statistical error counts replicas as correlated)" if sc_q is None else
            "data / hybrid model (errors: data, MC replicas, scan events) and data / MC as before",
            fontsize=9.5, loc="left")
a.grid(alpha=0.25, which="both")
fig.suptitle(("LNGS background, EACH PMT (runs 0011 + 0013) vs Monte Carlo: Hall A γ (J·A normalisation) + Hall A μ; "
              "scintillation of the fibre cores %s photons/MeV" % RESA) if sc_q is None else
             ("LNGS background, EACH PMT (runs 0011 + 0013) vs a HYBRID model: Monte Carlo (Hall A γ + μ) for the events "
              "with both PMTs lit, the self-trigger scan of 30/10 for the events with ONE PMT"), fontsize=11)
fig.tight_layout()
fig.savefig(arg.out, dpi=100)
print("scritto", arg.out)
