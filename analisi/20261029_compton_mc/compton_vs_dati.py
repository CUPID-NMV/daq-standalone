"""Spettro Compton simulato dei gamma di Hall A, visto dal WC prototipo,
sovrapposto alla carica dei PMT misurata al LNGS (run 0011).

    python3 analisi/20261029_compton_mc/compton_vs_dati.py MC_DIR OUT.png

MC_DIR = cartella `data/` del progetto Testbeam2026_WC_unified, con
sim_single_gamma_X0mm_Y0mm_Th0deg.root e sim_single_mu_lngs_X0mm_Y0mm_Th0deg.root
(geometria `single` = il prototipo). Serve uproot.

Il WC vede i gamma solo attraverso gli elettroni Compton (o fotoelettrici)
sopra la soglia Cherenkov in acqua. Il MC li segue fino ai fotoni che
arrivano ai due PMT; qui si applica la QE del PMT del prototipo (H14603-103,
la stessa di Signal_maker.ipynb), la risoluzione del singolo
fotoelettrone e il rumore del piedistallo MISURATO nei dati.

Normalizzazioni assolute, prese dal progetto MC (Background_final.ipynb):
  gamma: J = 0.28 cm^-2 s^-1 (Hall A, < 3 MeV, per cm^2 di superficie = phi/4), superficie che genera
         2*130*130 + 4*130*30 cm^2  ->  R = J * A = 13832 Hz (fino al 29/10: 4322)
  muoni: 3.0e-8 cm^-2 s^-1 su un piano di 650 x 650 cm -> 0.0127 Hz
Il rate di ogni evento MC e' quindi R / N_generati.

La scala ADC*ns per fotoelettrone NON e' misurata. Si ricava dai muoni:
mediana della carica per PMT dei muoni MC che attraversano l'acqua contro
la mediana dei muoni veri. Due stime, che fanno da banda:
  - Frascati 0005, tre SiPM sopra soglia (ch24, ch26);
  - i tre impulsi dei due muoni della run LNGS 0000 da 24 h.
"""
# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
_sys.path.insert(0, _os.path.join(ROOT, "tools"))

import json
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import uproot

MC, OUT = sys.argv[1], sys.argv[2]
MIS = _os.path.join(ROOT, "measurements")

# --- normalizzazioni del progetto MC ---
R_GAMMA_HZ = 0.28 * (2 * 130 * 130 + 4 * 130 * 30)   # J * A: analisi/20261029_normalizzazione_gamma/flusso_haffke.py
R_MU_HZ = 3.0e-8 * 650.0 ** 2
# --- PMT del prototipo (Signal_maker.ipynb) ---
QE_WL = [200, 250, 300, 350, 380, 400, 420, 450, 500, 550, 600, 650, 700]
QE = [0.18, 0.25, 0.30, 0.34, 0.35, 0.34, 0.32, 0.28, 0.18, 0.08, 0.03, 0.005, 0.0]
SPE_RES = 0.30
REPLICHE = 400          # ripetizioni di QE, guadagno e rumore sugli stessi fotoni
LIVE_0011 = 11992.98    # s, "Acquisition time" nel log della run 0011
MUONI_LNGS = [5827.0, 3633.0, 4602.0]   # ADC*ns, run LNGS 0000 eventi 17 e 24


def fotoelettroni(path, rng, replica_extra=1):
    """PE per PMT (0 = A, 1 = B) per ogni evento con fotoni, ripetuto."""
    f = uproot.open(path)
    n_gen = int(f["Eventi"].num_entries)
    tl = f["Eventi"].arrays(["TrackL_water_mm"], library="np")["TrackL_water_mm"]
    ph = f["Fotoni"].arrays(["EventID", "PMT_ID", "E_Hit_eV"], library="np")
    ev, pid = ph["EventID"].astype(np.int64), ph["PMT_ID"]
    qe = np.interp(1240.0 / ph["E_Hit_eV"], QE_WL, QE, left=0, right=0)
    uev, inv = np.unique(ev, return_inverse=True)
    out = []
    for _ in range(replica_extra):
        acc = rng.random(len(ev)) < qe
        g = np.maximum(0.0, rng.normal(1.0, SPE_RES, len(ev))) * acc
        qa = np.bincount(inv, weights=g * (pid == 0), minlength=len(uev))
        qb = np.bincount(inv, weights=g * (pid == 1), minlength=len(uev))
        out.append(np.stack([qa, qb], 1))
    return n_gen, uev, tl[uev], np.concatenate(out)


rng = np.random.default_rng(29)
ng_g, ev_g, _, pe_g = fotoelettroni(_os.path.join(MC, "sim_single_gamma_X0mm_Y0mm_Th0deg.root"), rng, REPLICHE)
ng_m, ev_m, tl_m, pe_m = fotoelettroni(_os.path.join(MC, "sim_single_mu_lngs_X0mm_Y0mm_Th0deg.root"), rng, 20)
w_g = R_GAMMA_HZ / ng_g / REPLICHE          # Hz per evento-replica
w_m = R_MU_HZ / ng_m / 20
tl_m = np.tile(tl_m, 20)

# --- dati ---
lng = np.load(_os.path.join(MIS, "carica_0011_testbeam.npz"))
q20, q21 = lng["q_caen_ch20"], lng["q_caen_ch21"]
fra = np.load(_os.path.join(MIS, "carica_frascati_0005_p0-150_g150-600.npz"))
sel = np.load(_os.path.join(MIS, "frascati_0005_sel_3sipm_500k.npy"))


def sigma_ped(q):
    x = q[np.abs(q) < 1500].astype(float)
    m, s = x.mean(), x.std()
    for _ in range(5):
        d = np.abs(x - m) < 3.5 * s
        m, s = x[d].mean(), x[d].std()
    return s


SIG = {20: sigma_ped(q20), 21: sigma_ped(q21)}
SIG_MC = 0.5 * (SIG[20] + SIG[21])
both = (q20 > 3 * SIG[20]) & (q21 > 3 * SIG[21])

# --- scala ADC*ns per PE dai muoni ---
mu_ok = (tl_m > 0) & (pe_m[:, 0] > 0) & (pe_m[:, 1] > 0)
pe_mu_med = float(np.median(pe_m[mu_ok].ravel()))
fra_med = float(np.median(np.concatenate([fra["q_caen_ch24"][sel], fra["q_caen_ch26"][sel]])))
K = {"Frascati muons": fra_med / pe_mu_med, "LNGS muons (3 pulses)": float(np.mean(MUONI_LNGS)) / pe_mu_med}
print("PE per PMT, mediana muoni MC che attraversano: %.2f" % pe_mu_med)
for k, v in K.items():
    print("scala %s: %.0f ADC*ns/PE" % (k, v))


def carica_mc(pe, k):
    return pe * k + rng.normal(0.0, SIG_MC, pe.shape)


b = np.linspace(-1000, 30000, 125)
bc = 0.5 * (b[1:] + b[:-1])
fig, ax = plt.subplots(1, 2, figsize=(18, 7))
risultati = {}
for a, ch, j in ((ax[0], 20, 0), (ax[1], 21, 1)):
    qd = (q20, q21)[j]
    hd, _ = np.histogram(qd[both], b)
    a.errorbar(bc, hd / LIVE_0011, yerr=np.sqrt(hd) / LIVE_0011, fmt="o", ms=3.5, color="k", lw=0.8,
               label="DATA LNGS 0011 ch%d, both PMT > 3σ_ped (%d ev., %.0f s)" % (ch, both.sum(), LIVE_0011))
    hall, _ = np.histogram(qd, b)
    a.step(bc, hall / LIVE_0011, where="mid", color="#999", lw=0.9,
           label="DATA LNGS 0011 ch%d, all events (self-trigger OR)" % ch)
    for (nome, k), cc, ls in zip(K.items(), ("#e8710a", "#d62728"), ("-", "--")):
        qg = carica_mc(pe_g, k)
        ok = (qg[:, 0] > 3 * SIG_MC) & (qg[:, 1] > 3 * SIG_MC)
        hg, _ = np.histogram(qg[ok, j], b)
        a.step(bc, hg * w_g, where="mid", color=cc, lw=1.8, ls=ls,
               label="MC Hall A γ (Compton), both > 3σ, scale %.0f ADC·ns/PE (%s): %.3f Hz"
                     % (k, nome, ok.sum() * w_g))
        qm = carica_mc(pe_m, k)
        okm = (qm[:, 0] > 3 * SIG_MC) & (qm[:, 1] > 3 * SIG_MC)
        hm, _ = np.histogram(qm[okm, j], b)
        a.step(bc, hm * w_m, where="mid", color="#2a6fdb", lw=1.3, ls=ls,
               label="MC LNGS μ, both > 3σ, same scale: %.2g Hz" % (okm.sum() * w_m))
        risultati["ch%d %s" % (ch, nome)] = {"k_adcns_per_pe": k, "rate_gamma_hz": ok.sum() * w_g,
                                             "rate_mu_hz": okm.sum() * w_m}
    for x in MUONI_LNGS:
        a.axvline(x, color="#2a6fdb", lw=0.8, ls=":")
    a.axvline(3 * SIG[ch], color="#888", lw=0.8, ls="-.")
    a.set_yscale("log")
    a.ticklabel_format(axis="x", style="plain")
    a.set_ylim(2e-6, 0.1)
    a.set_xlim(-1000, 30000)
    a.set_xlabel("PMT charge [ADC·ns]   (gate 240-500 ns, pedestal 0-150 ns, CAEN correction only)")
    a.set_ylabel("rate per bin [Hz] (bin = %.0f ADC·ns)" % (b[1] - b[0]))
    a.set_title("ch%d — absolute rates, no free normalisation (dotted blue: the two LNGS muons; "
                "dash-dot: 3σ_ped = %.0f)" % (ch, 3 * SIG[ch]), fontsize=9.5, loc="left")
    a.legend(fontsize=7.5, frameon=False, loc="upper right")
    a.grid(alpha=0.25, which="both")
    sec = a.secondary_xaxis("top", functions=(lambda x, k=K["Frascati muons"]: x / k,
                                              lambda p, k=K["Frascati muons"]: p * k))
    sec.set_xlabel("photoelectrons (scale from Frascati muons)", fontsize=9)
fig.suptitle("Hall A gamma background seen by the WC prototype (Geant4, Compton electrons → Cherenkov → fibres → PMT) "
             "vs LNGS data — MC: %d γ with photons at the PMTs out of %d generated (= %.0f s of Hall A)"
             % (len(ev_g), ng_g, ng_g / R_GAMMA_HZ), fontsize=11)
fig.tight_layout()
fig.savefig(OUT, dpi=100)
print(json.dumps(risultati, indent=1))
print("dati: both>3sigma %.3f Hz, tutti %.3f Hz" % (both.sum() / LIVE_0011, len(q20) / LIVE_0011))
