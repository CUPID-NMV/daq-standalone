"""Dati LNGS 0011, fondo Compton e muoni di Hall A simulati, normalizzati
allo STESSO tempo di acquisizione (quello della 0011), invece che in area.

    python3 analisi/20261029_compton_mc/normalizzazione_tempo.py MC_DIR OUT.png

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
fig, ax = plt.subplots(1, 2, figsize=(19, 7.4))
for a, ch, qd, gq, mq, fq in ((ax[0], 20, q20, gA, mA, f24), (ax[1], 21, q21, gB, mB, f26)):
    h, _ = np.histogram(qd[both], b)
    bc = 0.5 * (b[1:] + b[:-1])
    a.errorbar(bc[h > 0], h[h > 0], yerr=np.sqrt(h[h > 0]), fmt="o", ms=3.5, color="k", lw=0.8,
               label="DATA LNGS 0011 ch%d, both PMT > 3σ_ped: %d events in %.0f s" % (ch, both.sum(), T))
    hg, _ = np.histogram(gq, b)
    a.step(bc, hg * wg, where="mid", color="#e8710a", lw=2,
           label="MC Hall A γ → Compton e- → Cherenkov: %.0f events expected" % Ng)
    hm, _ = np.histogram(mq, b)
    a.step(bc, hm * wm, where="mid", color="#2a6fdb", lw=2,
           label="MC Hall A μ (Mei & Hime, MACRO angles): %.2f events expected" % Nm)
    a.step(bc, np.histogram(fq, b)[0] / len(fq) * Nm, where="mid", color="#2a6fdb", lw=1, ls=":",
           label="LNF muon shape (Frascati ch%d), scaled to the MC muon count" % (24 if ch == 20 else 26))
    hs = hg * wg + hm * wm
    a.step(bc, hs, where="mid", color="#7b2d8b", lw=1, ls="--", label="MC γ + μ")
    for k, (e, c_, v) in enumerate(MUONI):
        if c_ == ch:
            a.axvline(v, color="#7b2d8b", lw=2, ls=("-", "--", ":")[k],
                      label="LNGS muon candidate (24 h run), event %d: %.0f ADC·ns" % (e, v))
    a.axvline(3 * S[ch], color="#888", lw=0.8, ls="-.")
    a.set_yscale("log")
    a.set_ylim(1e-3, 3e3)
    a.set_xlim(-1000, 30000)
    a.ticklabel_format(axis="x", style="plain")
    a.set_xlabel("PMT charge, absolute value [ADC·ns]   (gate 240-500 ns, pedestal 0-150 ns)")
    a.set_ylabel("events per bin in %.0f s (bin = %.0f ADC·ns)" % (T, b[1] - b[0]))
    a.set_title("ch%d — everything normalised to the 0011 acquisition time (dash-dot: 3σ_ped = %.0f)"
                % (ch, 3 * S[ch]), fontsize=10, loc="left")
    a.legend(fontsize=7.6, frameon=False, loc="upper right")
    a.grid(alpha=0.25, which="both")
    sec = a.secondary_xaxis("top", functions=(lambda x: x / K, lambda p: p * K))
    sec.set_xlabel("photoelectrons (scale %.0f ADC·ns/PE from LNF muons vs MC muons)" % K, fontsize=9)
fig.suptitle("WC prototype at LNGS: data vs Geant4 Hall A γ (%.0f Hz, %.0f s simulated) and μ (0.0127 Hz, %.0f days simulated), "
             "time-normalised — self-trigger not simulated" % (R_GAMMA_HZ, ngg / R_GAMMA_HZ, ngm / R_MU_HZ / 86400), fontsize=11)
fig.tight_layout()
fig.savefig(OUT, dpi=100)
