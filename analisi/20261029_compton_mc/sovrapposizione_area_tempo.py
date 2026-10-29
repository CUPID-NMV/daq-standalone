"""Come sovrapposizione.py, ma il Compton NON e' normalizzato in area: e'
normalizzato al tempo di acquisizione della 0011 e diviso per il numero di
eventi LNGS, cosi' sta nella stessa unita' della curva LNGS (frazione degli
eventi LNGS per bin) e la sua area vale N_Compton_atteso / N_LNGS.

    python3 analisi/20261029_compton_mc/sovrapposizione_area_tempo.py MC_DIR OUT.png [RUN]

RUN = 0011 (di serie, self-trigger offset 200) oppure 0013 (offset 100).

- LNF: Frascati 0005, tre SiPM sopra soglia, cancello della 0011 allineato
  sull'impulso (318-578 ns, piedistallo 78-228);
- LNGS: 0011, entrambi i PMT sopra 3 sigma del piedistallo (240-500 ns, 0-150);
- muoni LNGS: i tre impulsi dei due muoni della run 0000 da 24 h;
- Compton: gamma di Hall A nel MC del prototipo (vedi compton_vs_dati.py),
  stessa selezione dei dati LNGS, scala ADC*ns/PE dai muoni di Frascati col
  cancello LNGS (mediana dei due PMT).

Normalizzare in area confronta le FORME; il rate assoluto del Compton
(che torna con quello dei dati) e' nel grafico di compton_vs_dati.py e
viene riportato nel riquadro.
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
QE_WL = [200, 250, 300, 350, 380, 400, 420, 450, 500, 550, 600, 650, 700]
QE = [0.18, 0.25, 0.30, 0.34, 0.35, 0.34, 0.32, 0.28, 0.18, 0.08, 0.03, 0.005, 0.0]
SPE_RES, REPLICHE, PE_MU_MC = 0.30, 400, 4.86
RUN = sys.argv[3] if len(sys.argv) > 3 else "0011"
# "Acquisition time" dei log delle run, e offset del self-trigger dei PMT
LIVE_0011 = {"0011": 11992.98, "0013": 2367.313986}[RUN]
OFFSET = {"0011": 200, "0013": 100}[RUN]
MUONI = [(17, 20, 5827.0), (17, 21, 3633.0), (24, 21, 4602.0)]

lng = np.load(_os.path.join(MIS, "carica_%s_testbeam.npz" % RUN))
q20, q21 = lng["q_caen_ch20"], lng["q_caen_ch21"]
fra = np.load(_os.path.join(MIS, "carica_frascati_0005_gateLNGS_ped78-228.npz"))
sel = np.load(_os.path.join(MIS, "frascati_0005_sel_3sipm_500k.npy"))
f24, f26 = fra["q_caen_ch24"][sel], fra["q_caen_ch26"][sel]
K = 0.5 * (np.median(f24) + np.median(f26)) / PE_MU_MC


def sigma_ped(q):
    x = q[np.abs(q) < 1500].astype(float)
    m, s = x.mean(), x.std()
    for _ in range(5):
        d = np.abs(x - m) < 3.5 * s
        m, s = x[d].mean(), x[d].std()
    return s


S20, S21 = sigma_ped(q20), sigma_ped(q21)
both = (q20 > 3 * S20) & (q21 > 3 * S21)

# --- MC Compton: fotoni -> QE -> PE -> ADC*ns, con il rumore misurato ---
rng = np.random.default_rng(29)
f = uproot.open(_os.path.join(MC, "sim_single_gamma_X0mm_Y0mm_Th0deg.root"))
ng = int(f["Eventi"].num_entries)
ph = f["Fotoni"].arrays(["EventID", "PMT_ID", "E_Hit_eV"], library="np")
ev, pid = ph["EventID"].astype(np.int64), ph["PMT_ID"]
qe = np.interp(1240.0 / ph["E_Hit_eV"], QE_WL, QE, left=0, right=0)
uev, inv = np.unique(ev, return_inverse=True)
qa, qb = [], []
for _ in range(REPLICHE):
    g = np.maximum(0.0, rng.normal(1.0, SPE_RES, len(ev))) * (rng.random(len(ev)) < qe)
    qa.append(np.bincount(inv, weights=g * (pid == 0), minlength=len(uev)))
    qb.append(np.bincount(inv, weights=g * (pid == 1), minlength=len(uev)))
sig = 0.5 * (S20 + S21)
qa = np.concatenate(qa) * K + rng.normal(0, sig, len(uev) * REPLICHE)
qb = np.concatenate(qb) * K + rng.normal(0, sig, len(uev) * REPLICHE)
okg = (qa > 3 * sig) & (qb > 3 * sig)
rate_g = okg.sum() * R_GAMMA_HZ / ng / REPLICHE
mc = np.concatenate([qa[okg], qb[okg]])
# eventi Compton attesi nel tempo della 0011, per PMT, in unita' di eventi LNGS
W_MC = R_GAMMA_HZ * LIVE_0011 / ng / REPLICHE / 2.0 / both.sum()

b = np.linspace(-1000, 30000, 125)
W = lambda x: np.full(len(x), 1.0 / len(x))
fig, ax = plt.subplots(1, 2, figsize=(19, 7.2))
for a, log in ((ax[0], False), (ax[1], True)):
    a.hist(f24, b, weights=W(f24), histtype="step", color="#2a6fdb", lw=1.4,
           label="LNF (Frascati 0005) ch24, 3 SiPM > 500000, LNGS gate (%d ev.)" % len(f24))
    a.hist(f26, b, weights=W(f26), histtype="step", color="#1aa39a", lw=1.4,
           label="LNF (Frascati 0005) ch26, idem")
    a.hist(q20[both], b, weights=W(q20[both]), histtype="step", color="#e8710a", lw=1.6,
           label="LNGS %s ch20 (self-trigger offset %d), both PMT > 3σ_ped (%d ev. in %.0f s)" % (RUN, OFFSET, both.sum(), LIVE_0011))
    a.hist(q21[both], b, weights=W(q21[both]), histtype="step", color="#c23b5a", lw=1.6,
           label="LNGS %s ch21, idem" % RUN)
    a.hist(mc, b, weights=np.full(len(mc), W_MC), color="#888", alpha=0.35, lw=0,
           label="MC Hall A γ → Compton e- → Cherenkov, both > 3σ, TIME-normalised to the LNGS %s run "
                 "(area = %.2f × LNGS)" % (RUN, len(mc) * W_MC))
    for k, (e, ch, v) in enumerate(MUONI):
        a.axvline(v, color="#7b2d8b", lw=2.2, ls=("-", "--", ":")[k],
                  label="LNGS muon candidate %s: event %d, ch%d = %.0f ADC·ns" % ("abc"[k], e, ch, v))
    a.set_xlim(-1000, 30000)
    a.ticklabel_format(axis="x", style="plain")
    a.set_xlabel("PMT charge, absolute value [ADC·ns]   (1 pC = 204.8 ADC·ns)")
    a.set_ylabel("fraction of events / bin (bin = %.0f ADC·ns); LNF, LNGS: area = 1" % (b[1] - b[0]))
    a.grid(alpha=0.25, which="both")
    sec = a.secondary_xaxis("top", functions=(lambda x: x / K, lambda p: p * K))
    sec.set_xlabel("photoelectrons (scale from LNF muons vs MC muons)", fontsize=9)
    if log:
        a.set_yscale("log")
        a.set_ylim(1e-5, 1.0)
        a.set_title("log scale", fontsize=10, loc="left")
    else:
        a.set_title("linear scale", fontsize=10, loc="left")
        a.legend(fontsize=7.6, frameon=False, loc="upper right")
        a.text(0.98, 0.42, "absolute rates, both PMT > 3σ_ped:\nLNGS %s data  %.3f Hz\nMC Hall A γ (Compton)  %.3f Hz"
               % (RUN, both.sum() / LIVE_0011, rate_g), transform=a.transAxes, ha="right", va="top", fontsize=9,
               bbox=dict(boxstyle="round", fc="white", ec="#bbb"))
fig.suptitle("WC prototype PMT charge: LNF muons, LNGS data, LNGS muon candidates and the simulated "
             "Hall A Compton background (data area-normalised, Compton normalised to the LNGS acquisition time; scale %.0f ADC·ns/PE)" % K,
             fontsize=11)
fig.tight_layout()
fig.savefig(OUT, dpi=100)
print("scala %.0f ADC*ns/PE, rate MC Compton %.3f Hz, dati %.3f Hz" % (K, rate_g, both.sum() / LIVE_0011))
