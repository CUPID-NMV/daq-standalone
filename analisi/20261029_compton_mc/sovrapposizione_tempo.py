"""Come sovrapposizione.py (LNF, LNGS, candidati muoni, Compton), ma con il
Compton normalizzato al TEMPO di acquisizione della 0011 invece che in area.

    python3 analisi/20261029_compton_mc/sovrapposizione_tempo.py MC_DIR OUT.png

Asse y: eventi per bin in 11993 s. Dati LNGS e Compton MC sono in valore
assoluto. LNF (Frascati) non si puo' normalizzare al tempo -- altro sito,
altro trigger -- e viene disegnato con la sua forma, riscalato allo stesso
numero di eventi dei dati LNGS sopra 6000 ADC*ns: serve a confrontare la
pendenza della coda, non e' una predizione.

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


b = np.linspace(-1000, 30000, 125)
bc = 0.5 * (b[1:] + b[:-1])
CODA = 6000.0
dati = {20: q20[both], 21: q21[both]}
lnf = {24: f24, 26: f26}
mcg = np.concatenate([gA, gB])          # Compton, i due PMT insieme
hg = np.histogram(mcg, b)[0] * wg / 2   # per PMT
fig, ax = plt.subplots(1, 2, figsize=(19, 7.4))
for a, log in ((ax[0], False), (ax[1], True)):
    for (chf, qf), cf in zip(lnf.items(), ("#2a6fdb", "#1aa39a")):
        nd = 0.5 * sum((dati[c] > CODA).sum() for c in dati)
        sc = nd / (qf > CODA).sum()
        a.step(bc, np.histogram(qf, b)[0] * sc, where="mid", color=cf, lw=1.3,
               label="LNF (Frascati 0005) ch%d, 3 SiPM > 500000, shape scaled to LNGS above %.0f ADC·ns"
                     % (chf, CODA))
    for (ch, qd), cd in zip(dati.items(), ("#e8710a", "#c23b5a")):
        h = np.histogram(qd, b)[0]
        a.errorbar(bc[h > 0], h[h > 0], yerr=np.sqrt(h[h > 0]), fmt="o", ms=3, color=cd, lw=0.8,
                   label="LNGS 0011 ch%d, both PMT > 3σ_ped: %d events in %.0f s" % (ch, len(qd), T))
    a.fill_between(bc, hg, step="mid", color="#888", alpha=0.35, lw=0,
                   label="MC Hall A γ → Compton e- → Cherenkov, per PMT, time-normalised: %.0f events" % Ng)
    for k, (e, ch, v) in enumerate(MUONI):
        a.axvline(v, color="#7b2d8b", lw=2.2, ls=("-", "--", ":")[k],
                  label="LNGS muon candidate %s: event %d, ch%d = %.0f ADC·ns" % ("abc"[k], e, ch, v))
    a.set_xlim(-1000, 30000)
    a.ticklabel_format(axis="x", style="plain")
    a.set_xlabel("PMT charge, absolute value [ADC·ns]   (1 pC = 204.8 ADC·ns)")
    a.set_ylabel("events per bin in %.0f s (bin = %.0f ADC·ns)" % (T, b[1] - b[0]))
    a.grid(alpha=0.25, which="both")
    sec = a.secondary_xaxis("top", functions=(lambda x: x / K, lambda p: p * K))
    sec.set_xlabel("photoelectrons (scale %.0f ADC·ns/PE from LNF muons vs MC muons)" % K, fontsize=9)
    if log:
        a.set_yscale("log")
        a.set_ylim(0.5, 1000)
        a.set_title("log scale", fontsize=10, loc="left")
    else:
        a.set_ylim(0, 120)
        a.set_title("linear scale (y cut at 120: the Compton peak reaches ~%.0f)" % hg.max(), fontsize=10, loc="left")
        a.legend(fontsize=7.4, frameon=False, loc="upper right")
fig.suptitle("WC prototype PMT charge: LNF muons, LNGS data, LNGS muon candidates and the Hall A Compton background "
             "normalised to the LNGS acquisition time (%.0f Hz γ, %.0f s simulated; self-trigger not simulated)"
             % (R_GAMMA_HZ, ngg / R_GAMMA_HZ), fontsize=11)
fig.tight_layout()
fig.savefig(OUT, dpi=100)
