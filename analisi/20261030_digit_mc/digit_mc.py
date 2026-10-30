"""Digitalizza il MC del WC prototipo come i dati veri della catena LNGS, e
gli applica la stessa analisi e lo stesso trigger.

    python3 analisi/20261030_digit_mc/digit_mc.py MC_DIR NOME OUT.npz [REPLICHE] [--k K] [--rate HZ] [--geo GEO]

NOME seleziona i file MC: sim_<GEO>_<NOME>_X0mm_Y0mm_Th0deg.root oppure le
parti sim_<GEO>_<NOME>_part*_X0mm_Y0mm_Th0deg.root (Run_gamma_parallel.sh);
GEO di serie e' single. Con GEO = single_pan (i tre pannelli EJ-200 sotto il
serbatoio) si salva anche l'energia depositata in ciascun pannello, e il
rate di serie dei gamma e' quello della scatola di
gun_gammas_background_righe_pannelli.mac (81400 cm^2).

Perche' non Signal_maker: quello rappresenta la catena del test beam (PMT
senza amplificatore, singolo PE da ~3 mV su 3.5 conteggi di rumore bianco):
al LNGS il segnale passa dalla scheda di amplificazione e il PE sta molto
sopra il rumore, e il rumore vero non e' bianco. Con Signal_maker riportato
alla nostra scala il rumore integrato nel cancello valeva ~6 PE.

Catena, evento per evento:
  1. fotoni ai due PMT dal MC (tempo di arrivo); QE del fotocatodo super
     bialkali con finestra UV del PMT del prototipo (Hamamatsu H14600 serie,
     variante -103: 185-650 nm, 110 mA/W a 400 nm cioe' QE 34%, datasheet
     TPMO1097E05; la stessa curva di Signal_maker.ipynb); TTS 0.3 ns FWHM e
     risoluzione del singolo PE 40% come Signal_maker (il datasheet non li da');
  2. allineamento come nel self-trigger: il primo PE dell'evento a T_TRIG,
     il passaggio al 50% misurato negli impulsi veri della 0013;
  3. ogni PE e' la forma media misurata degli impulsi veloci della 0013
     (area 1, libreria_digit_0013.npz) per K * guadagno: contiene PMT,
     scheda di amplificazione, coda e sottoelongazione;
  4. l'impulso (negativo) si somma a una traccia VERA di solo piedistallo
     della 0013 dello stesso canale: rumore, ondulazione della base,
     oscillazione iniziale e struttura del DRS4 sono quelli veri. Le tracce
     dei due canali vengono da eventi diversi: la parte di rumore comune ai
     due canali non c'e';
  5. stessa analisi dei dati: piedistallo 0-150 ns, cancello 240-500 ns,
     carica = somma (piedistallo - ADC) * dt, ampiezza = massimo nel cancello;
  6. trigger: self-trigger in OR, ampiezza oltre il bordo misurato nella 0013
     (310 conteggi su ch20, 319 su ch21, offset 100).

K (ADC*ns per PE: guadagno del PMT per quello della scheda di
amplificazione) non e' misurato: con --k si impone, altrimenti si usa
measurements/mc_digit_K.json, scritto da calibra_k.py sui muoni.

Salva per ogni evento con almeno un fotone: carica e ampiezza dei due PMT,
il trigger, la traccia in acqua, e il peso in Hz (rate / generati / repliche).
"""
# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))

import argparse
import glob
import json
import numpy as np
import uproot

ap = argparse.ArgumentParser()
ap.add_argument("mc_dir")
ap.add_argument("nome")
ap.add_argument("out")
ap.add_argument("repliche", nargs="?", type=int, default=1)
ap.add_argument("--k", type=float, default=None, help="ADC*ns per fotoelettrone")
ap.add_argument("--geo", default="single", help="geometria del MC (nome dei file): single, single_pan, ...")
ap.add_argument("--rate", type=float, default=None,
                help="Hz della sorgente (di serie: 13832 per i gamma, 0.0127 per i muoni)")
a = ap.parse_args()

MIS = _os.path.join(ROOT, "measurements")
LIB = np.load(_os.path.join(MIS, "libreria_digit_0013.npz"))
DT = float(LIB["dt_ns"])
NS = LIB["rumore_ch20"].shape[1]
T = np.arange(NS) * DT
PED = T < 150
GATE = (T >= 240) & (T < 500)
XF = LIB["forma_x_ns"]
FORMA = {0: LIB["forma_ch20"], 1: LIB["forma_ch21"]}
RUMORE = {0: LIB["rumore_ch20"], 1: LIB["rumore_ch21"]}
SOGLIA = np.array([LIB["sogliaA_ch20"], LIB["sogliaA_ch21"]])
T_TRIG = 0.5 * (float(LIB["t50_ch20"]) + float(LIB["t50_ch21"]))
QE_WL = [200, 250, 300, 350, 380, 400, 420, 450, 500, 550, 600, 650, 700]
QE = [0.18, 0.25, 0.30, 0.34, 0.35, 0.34, 0.32, 0.28, 0.18, 0.08, 0.03, 0.005, 0.0]
SPE_RES = 0.40
TTS_SIGMA = 0.3 / 2.355
if a.k is None:
    a.k = json.load(open(_os.path.join(MIS, "mc_digit_K.json")))["K_adcns_per_pe"]
if a.rate is None:
    # gamma: J * A_gen, con A_gen la scatola delle macro (130 x 130 x 30 cm) e J = 0.28 cm^-2 s^-1
    # il valore di Hall A di arXiv:1101.5298, che conta i gamma ENTRANTI per cm^2 di superficie
    # del NaI, cioe' J = phi/4 (analisi/20261029_normalizzazione_gamma/flusso_haffke.py). Fino
    # al 2026-10-29 qui c'era 0.35/4 * A_gen: il 4 veniva diviso due volte, 3.2 volte meno gamma.
    # single_pan: la scatola della macro coi pannelli, 190 x 130 x 50 cm
    a_gen = (2 * 190 * 130 + 2 * 190 * 50 + 2 * 130 * 50) if a.geo == "single_pan" else (2 * 130 * 130 + 4 * 130 * 30)
    a.rate = 0.0127 if "mu" in a.nome else 0.28 * a_gen

files = sorted(glob.glob(_os.path.join(a.mc_dir, "sim_%s_%s_part*_X0mm_Y0mm_Th0deg.root" % (a.geo, a.nome)))) \
    or [_os.path.join(a.mc_dir, "sim_%s_%s_X0mm_Y0mm_Th0deg.root" % (a.geo, a.nome))]
rng = np.random.default_rng(7)


def onda(tempi, g, ch):
    """Impulso in conteggi (positivo) sulla griglia dei campioni."""
    w = np.zeros(NS)
    for ti, gi in zip(tempi, g):
        w += a.k * gi * np.interp(T - ti, XF, FORMA[ch], left=0.0, right=0.0)
    return w


Q, A, TL, NPE, EF, EP = [], [], [], [], [], []
n_gen = 0
for fn in files:
    f = uproot.open(fn)
    n_gen += int(f["Eventi"].num_entries)
    tl_all = f["Eventi"].arrays(["TrackL_water_mm"], library="np")["TrackL_water_mm"]
    # energia nel nucleo delle fibre (L, tratto dritto, R): c'e' solo nei file
    # prodotti dopo il 2026-10-29, con la scintillazione delle fibre
    try:
        ef_all = np.stack([f["Eventi"].arrays([k], library="np")[k] for k in
                           ("Edep_fiber_L_MeV", "Edep_fiber_C_MeV", "Edep_fiber_R_MeV")], 1)
    except Exception:
        ef_all = np.full((len(tl_all), 3), np.nan)
    # energia nei tre pannelli (alto, mezzo, basso): solo nei file della geometria single_pan
    try:
        ep_all = np.stack([f["Eventi"].arrays([k], library="np")[k] for k in
                           ("Edep_panel_top_MeV", "Edep_panel_mid_MeV", "Edep_panel_bot_MeV")], 1)
    except Exception:
        ep_all = np.full((len(tl_all), 3), np.nan)
    ph = f["Fotoni"].arrays(["EventID", "PMT_ID", "E_Hit_eV", "Arrival_Time_ns"], library="np")
    ev = ph["EventID"].astype(np.int64)
    pid = ph["PMT_ID"].astype(int)
    tt = ph["Arrival_Time_ns"]
    qe = np.interp(1240.0 / ph["E_Hit_eV"], QE_WL, QE, left=0, right=0)
    o = np.argsort(ev, kind="stable")
    ev, pid, tt, qe = ev[o], pid[o], tt[o], qe[o]
    uev, start = np.unique(ev, return_index=True)
    stop = np.append(start[1:], len(ev))
    for _ in range(a.repliche):
        acc = rng.random(len(ev)) < qe
        for e, s0, s1 in zip(uev, start, stop):
            k = np.arange(s0, s1)[acc[s0:s1]]
            if len(k) == 0:
                continue                             # nessun PE: solo rumore, il trigger non scatta
            tj = tt[k] + rng.normal(0, TTS_SIGMA, len(k))
            tj = tj - tj.min() + T_TRIG
            g = np.maximum(0.0, rng.normal(1.0, SPE_RES, len(k)))
            q_ev, a_ev, n_ev = [], [], []
            for ch in (0, 1):
                m = pid[k] == ch
                adc = RUMORE[ch][rng.integers(len(RUMORE[ch]))].astype(float) - onda(tj[m], g[m], ch)
                adc = np.clip(np.round(adc), 0, 4095)           # fondo scala del digitizer
                y = adc[PED].mean() - adc
                q_ev.append(y[GATE].sum() * DT)
                a_ev.append(y[GATE].max())
                n_ev.append(int(m.sum()))
            Q.append(q_ev)
            A.append(a_ev)
            NPE.append(n_ev)
            TL.append(tl_all[e])
            EF.append(ef_all[e])
            EP.append(ep_all[e])
    print("%s: %d eventi generati finora, %d digitalizzati" % (_os.path.basename(fn), n_gen, len(Q)))

Q, A = np.array(Q), np.array(A)
trig = (A[:, 0] >= SOGLIA[0]) | (A[:, 1] >= SOGLIA[1])
w = a.rate / n_gen / a.repliche
np.savez_compressed(a.out, q=Q, amp=A, npe=np.array(NPE), trig=trig, trackl=np.array(TL),
                    edep_fibre=np.array(EF), edep_pannelli=np.array(EP), geo=a.geo, peso_hz=w,
                    n_gen=n_gen, rate_hz=a.rate, k=a.k, repliche=a.repliche, soglia=SOGLIA, t_trig=T_TRIG,
                    file_mc=np.array([_os.path.basename(x) for x in files]))
print("scritto %s: %d eventi generati, %d con PE, %d col trigger; K = %.0f ADC*ns/PE; "
      "rate col trigger %.4f Hz" % (a.out, n_gen, len(Q), trig.sum(), a.k, trig.sum() * w))
