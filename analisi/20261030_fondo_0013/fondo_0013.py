"""Fondo LNGS: run 0011 (self-trigger offset 200) e 0013 (offset 100) contro
il fondo MC (Compton + muoni di Hall A), tutto in RATE (Hz per bin).

    python3 analisi/20261030_fondo_0013/fondo_0013.py MC_DIR OUT.png

Stesse normalizzazioni, selezione e scala di
analisi/20261029_compton_mc/normalizzazione_tempo.py, da cui e' derivato.
La sigma del piedistallo si misura run per run.

Normalizzazioni del progetto MC (Testbeam2026_WC_unified):
  gamma: J = 0.28 cm^-2 s^-1 (Hall A, < 3 MeV, arXiv:1101.5298; per cm^2 di superficie, = phi/4), superficie
         che genera 2*130*130 + 4*130*30 cm^2 -> R = J * A = 13832 Hz (fino al 29/10: phi/4 * A con phi = 0.35, 4322 Hz)
         (CLAUDE.md, "La normalizzazione dei gamma"; Background_final.ipynb)
  muoni: gun_muons_lngs.mac, Mei & Hime, angoli da MACRO, piano 650x650 cm,
         3.0e-8 cm^-2 s^-1 -> 0.0127 Hz
Un evento MC vale R / N_generati Hz; per T secondi, R * T / N_generati eventi.

Selezione uguale per dati e MC: entrambi i PMT sopra 3 sigma del piedistallo
(sigma misurata nei dati, nel cancello 240-500 ns). Il MC ha la QE del PMT
del prototipo, la risoluzione del singolo fotoelettrone e il rumore misurato;
la scala ADC*ns/PE viene dai muoni di Frascati contro i muoni MC (mediana).
Il self-trigger della 0011 NON e' simulato.

Lo spettro dei muoni di Frascati (LNF) e' disegnato con la FORMA misurata e
l'area dei muoni MC attesi: dice se la scala e la forma dei muoni tornano,
non e' un'altra predizione.
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
import uproot

MC, OUT = sys.argv[1], sys.argv[2]
MIS = _os.path.join(ROOT, "measurements")
R_GAMMA_HZ = 0.28 * (2 * 130 * 130 + 4 * 130 * 30)   # J * A: analisi/20261029_normalizzazione_gamma/flusso_haffke.py
R_MU_HZ = 3.0e-8 * 650.0 ** 2
QE_WL = [200, 250, 300, 350, 380, 400, 420, 450, 500, 550, 600, 650, 700]
QE = [0.18, 0.25, 0.30, 0.34, 0.35, 0.34, 0.32, 0.28, 0.18, 0.08, 0.03, 0.005, 0.0]
SPE_RES = 0.30
T = 11992.98                       # s, acquisizione della 0011
MUONI = [(17, 20, 5827.0), (17, 21, 3633.0), (24, 21, 4602.0)]

lng = np.load(_os.path.join(MIS, "carica_0011_testbeam.npz"))
q20, q21 = lng["q_caen_ch20"], lng["q_caen_ch21"]
fra = np.load(_os.path.join(MIS, "carica_frascati_0005_gateLNGS_ped78-228.npz"))
sel = np.load(_os.path.join(MIS, "frascati_0005_sel_3sipm_500k.npy"))
f24, f26 = fra["q_caen_ch24"][sel], fra["q_caen_ch26"][sel]


def sigma_ped(q):
    x = q[np.abs(q) < 1500].astype(float)
    m, s = x.mean(), x.std()
    for _ in range(5):
        d = np.abs(x - m) < 3.5 * s
        m, s = x[d].mean(), x[d].std()
    return s


S = {20: sigma_ped(q20), 21: sigma_ped(q21)}
both = (q20 > 3 * S[20]) & (q21 > 3 * S[21])
rng = np.random.default_rng(29)


def mc(nome, repl):
    """PE per PMT (A, B) di ogni evento con fotoni, ripetuto repl volte."""
    f = uproot.open(_os.path.join(MC, "sim_single_%s_X0mm_Y0mm_Th0deg.root" % nome))
    ng = int(f["Eventi"].num_entries)
    tl = f["Eventi"].arrays(["TrackL_water_mm"], library="np")["TrackL_water_mm"]
    ph = f["Fotoni"].arrays(["EventID", "PMT_ID", "E_Hit_eV"], library="np")
    ev, pid = ph["EventID"].astype(np.int64), ph["PMT_ID"]
    qe = np.interp(1240.0 / ph["E_Hit_eV"], QE_WL, QE, left=0, right=0)
    uev, inv = np.unique(ev, return_inverse=True)
    pa, pb = [], []
    for _ in range(repl):
        g = np.maximum(0.0, rng.normal(1.0, SPE_RES, len(ev))) * (rng.random(len(ev)) < qe)
        pa.append(np.bincount(inv, weights=g * (pid == 0), minlength=len(uev)))
        pb.append(np.bincount(inv, weights=g * (pid == 1), minlength=len(uev)))
    return ng, np.tile(tl[uev], repl), np.concatenate(pa), np.concatenate(pb)


ngg, _, pag, pbg = mc("gamma", 400)
ngm, tlm, pam, pbm = mc("mu_lngs", 20)
mu_ok = (tlm > 0) & (pam > 0) & (pbm > 0)
K = 0.5 * (np.median(f24) + np.median(f26)) / np.median(np.concatenate([pam[mu_ok], pbm[mu_ok]]))
sig = 0.5 * (S[20] + S[21])


def cariche(pa, pb):
    qa = pa * K + rng.normal(0, sig, len(pa))
    qb = pb * K + rng.normal(0, sig, len(pb))
    ok = (qa > 3 * sig) & (qb > 3 * sig)
    return qa[ok], qb[ok]


gA, gB = cariche(pag, pbg)
mA, mB = cariche(pam, pbm)
wg = R_GAMMA_HZ * T / ngg / 400        # eventi attesi in T per evento-replica
wm = R_MU_HZ * T / ngm / 20
Ng, Nm = len(gA) * wg, len(mA) * wm
print("scala %.0f ADC*ns/PE" % K)
print("attesi in %.0f s: Compton %.0f eventi, muoni %.2f; dati %d" % (T, Ng, Nm, both.sum()))


L13 = 2367.313986          # s, "Acquisition time" della 0013
r13 = np.load(_os.path.join(MIS, "carica_0013_testbeam.npz"))
p20, p21 = r13["q_caen_ch20"], r13["q_caen_ch21"]
S13 = {20: sigma_ped(p20), 21: sigma_ped(p21)}
both13 = (p20 > 3 * S13[20]) & (p21 > 3 * S13[21])
print("sigma ped 0011", S, "0013", S13)
print("0013: %d eventi, entrambi > 3 sigma %d (%.3f Hz); 0011: %d (%.3f Hz)"
      % (len(p20), both13.sum(), both13.sum() / L13, both.sum(), both.sum() / T))

b = np.linspace(-1000, 30000, 125)
bc = 0.5 * (b[1:] + b[:-1])
rg = R_GAMMA_HZ / ngg / 400       # Hz per evento-replica
rm = R_MU_HZ / ngm / 20
fig = plt.figure(figsize=(19, 10))
gs = fig.add_gridspec(2, 2, height_ratios=[3, 1], hspace=0.06)
tab = []
for col, (ch, q11, q13, gq, mq) in enumerate(((20, q20, p20, gA, mA), (21, q21, p21, gB, mB))):
    a = fig.add_subplot(gs[0, col])
    r = fig.add_subplot(gs[1, col], sharex=a)
    hg = np.histogram(gq, b)[0] * rg
    hm = np.histogram(mq, b)[0] * rm
    hs = hg + hm
    h11 = np.histogram(q11[both], b)[0]
    h13 = np.histogram(q13[both13], b)[0]
    a.step(bc, np.histogram(q13, b)[0] / L13, where="mid", color="#bbb", lw=0.9,
           label="LNGS 0013 ch%d, all events (no both-PMT cut)" % ch)
    a.errorbar(bc[h11 > 0], h11[h11 > 0] / T, yerr=np.sqrt(h11[h11 > 0]) / T, fmt="o", ms=3, color="#e8710a",
               lw=0.7, label="LNGS 0011, offset 200: both PMT > 3σ, %d ev. in %.0f s" % (both.sum(), T))
    a.errorbar(bc[h13 > 0], h13[h13 > 0] / L13, yerr=np.sqrt(h13[h13 > 0]) / L13, fmt="o", ms=3.5, color="k",
               lw=0.8, label="LNGS 0013, offset 100: both PMT > 3σ, %d ev. in %.0f s" % (both13.sum(), L13))
    a.step(bc, hs, where="mid", color="#7b2d8b", lw=2.2,
           label="MC background = Hall A γ (Compton) + μ: %.3f Hz" % (hs.sum()))
    a.step(bc, hm, where="mid", color="#2a6fdb", lw=1, ls="--", label="  of which muons: %.1e Hz" % hm.sum())
    a.axvline(3 * S13[ch], color="#888", lw=0.8, ls="-.")
    a.set_yscale("log")
    a.set_ylim(1e-6, 1)
    a.set_xlim(-1000, 30000)
    a.tick_params(labelbottom=False)
    a.set_ylabel("rate per bin [Hz] (bin = %.0f ADC·ns)" % (b[1] - b[0]))
    a.set_title("ch%d — LNGS background, offset 200 vs 100, and the MC background (rates, no free normalisation)"
                % ch, fontsize=10, loc="left")
    a.legend(fontsize=7.8, frameon=False, loc="upper right")
    a.grid(alpha=0.25, which="both")
    sec = a.secondary_xaxis("top", functions=(lambda x: x / K, lambda p: p * K))
    sec.set_xlabel("photoelectrons (scale %.0f ADC·ns/PE)" % K, fontsize=9)
    for hh, LL, cc, mk in ((h11, T, "#e8710a", "o"), (h13, L13, "k", "o")):
        ok = (hs > 0) & (hh > 0)
        r.errorbar(bc[ok], hh[ok] / LL / hs[ok], yerr=np.sqrt(hh[ok]) / LL / hs[ok], fmt=mk, ms=3, color=cc, lw=0.7)
    r.axhline(1, color="#7b2d8b", lw=1)
    r.set_yscale("log")
    r.set_ylim(1e-2, 1e5)
    r.ticklabel_format(axis="x", style="plain")
    r.set_xlabel("PMT charge, absolute value [ADC·ns]   (gate 240-500 ns, pedestal 0-150 ns)")
    r.set_ylabel("data / MC")
    r.grid(alpha=0.25, which="both")
    for lo, hi in ((0, 2500), (2500, 5000), (5000, 30000)):
        s_ = (bc > lo) & (bc < hi)
        tab.append((ch, lo, hi, h11[s_].sum() / T, h13[s_].sum() / L13, hg[s_].sum(), hm[s_].sum()))
fig.suptitle("LNGS background with the PMT self-trigger at offset 200 (0011) and 100 (0013) vs MC Hall A γ + μ "
             "(%.0f s of γ simulated: MC statistics is the limit)" % (ngg / R_GAMMA_HZ), fontsize=11)
fig.savefig(OUT, dpi=100, bbox_inches="tight")
print("ch  intervallo ADC*ns    0011[Hz]  0013[Hz]  MC gamma[Hz]  MC mu[Hz]")
for ch, lo, hi, a11, a13, g, m in tab:
    print("%d  %5d-%5d        %.4f    %.4f    %.4f      %.1e" % (ch, lo, hi, a11, a13, g, m))
