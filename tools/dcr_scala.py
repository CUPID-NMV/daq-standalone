#!/usr/bin/env python3
"""Rate degli impulsi al buio (DCR) di un SiPM in funzione della soglia, da
una run a TRIGGER SOFTWARE: la "scala" a gradini, il crosstalk, e il DCR a
0.5, 1.5 e 2.5 fotoelettroni.

    python3 tools/dcr_scala.py data/test_54-0V_0003_750Ms_40PT.h5.gz --canali 16 17 18

Perche' una run a trigger software. Il rate di una run in self-trigger e'
quello della DAQ satura (~975 Hz a ogni tensione, CLAUDE.md), e prima del
trigger mancano proprio gli impulsi che l'avrebbero fatto scattare. Una
finestra aperta dal PC senza guardare il segnale e' invece un campione
imparziale di un processo di Poisson: si contano gli impulsi che ci cadono.

Lo scan in soglia si fa QUI, offline, sugli stessi dati: una sola run per
tensione da' tutta la curva. Fra due campioni consecutivi in salita, y_t <=
s < y_t+1, la traccia attraversa la soglia s: il numero di attraversamenti a
ogni soglia si ottiene da due ordinamenti, senza un ciclo per soglia.

La scala in fotoelettroni non viene da una calibrazione mV/offset ma dalla
curva stessa: la sua derivata e' lo spettro delle ampiezze degli impulsi al
buio, con i picchi a 1, 2, 3 p.e. Il DCR si legge a meta' fra i picchi.

Tre stime del DCR, che devono andare d'accordo dove valgono:
  conteggio   attraversamenti / (eventi x durata). Sottostima col pile-up:
              un impulso che arriva mentre il precedente e' ancora sopra
              soglia non attraversa.
  tempo morto il conteggio corretto come un tempo morto non paralizzabile,
              m / (1 - m tau), con tau = tempo medio sopra soglia per
              attraversamento, misurato sui dati.
  Poisson     dalla frazione di finestre SENZA attraversamenti, P0 =
              exp(-DCR T): DCR = -ln(P0) / T. Robusto al pile-up, ma perde
              precisione quando quasi tutte le finestre hanno un impulso
              (P0 -> 0, tipicamente a 0.5 p.e. con DCR di MHz).

Due controlli che il campione sia davvero cieco: il DCR nella prima e nella
seconda meta' della finestra (devono coincidere: un disturbo legato al
trigger cadrebbe sempre nello stesso punto), e il tipo di trigger scritto nel
file (deve essere "Software" e basta).

Le soglie sotto ~3 sigma del rumore filtrato contano soprattutto il rumore:
il grafico le ombreggia.
"""

import argparse
import json
import math
import os
import sys

import numpy as np
from scipy.signal import butter, find_peaks, sosfiltfilt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daqio        # noqa: E402
import efficienza   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LP_MHZ = efficienza.LP_MHZ          # 50 MHz, lo stesso della stima dell'efficienza
LP_ORDINE = efficienza.LP_ORDINE
MV_PER_CONTEGGIO = efficienza.MV_PER_CONTEGGIO
# Gli ultimi campioni portano spesso un picco positivo spurio del V1742 (lo
# stesso motivo per cui il monitor li scarta): contati, sarebbero impulsi.
SCARTA_CODA = 20
BLOCCO = 2000


def piedistallo_dal_basso(y, giri=4):
    """Piedistallo e rumore di ogni evento, presi dal lato SENZA impulsi.

    A MHz di impulsi al buio, lunghi centinaia di ns, una buona parte della
    finestra sta dentro un impulso: la mediana della traccia sale con loro.
    Gli impulsi stanno tutti da una parte (qui: sopra), il rumore e'
    simmetrico. Si stima quindi la larghezza del rumore dai soli campioni
    sotto il piedistallo, si tengono quelli entro 2 sigma sopra, e si rifa'
    la mediana; in pochi giri converge sul livello piu' popolato.
    """
    b = np.median(y, axis=1)
    s = np.ones_like(b)
    for _ in range(giri):
        sotto = np.where(y < b[:, None], b[:, None] - y, np.nan)
        s = 1.4826 * np.nanmedian(sotto, axis=1)
        s = np.where(np.isfinite(s) & (s > 0), s, 1.0)
        tieni = np.where(y < (b + 2.0 * s)[:, None], y, np.nan)
        b = np.nanmedian(tieni, axis=1)
    return b, s


def conta_blocco(y, soglie, isteresi, tratto=None):
    """Conteggi di un blocco di tracce gia' filtrate e senza piedistallo.

    Un impulso conta quando la traccia passa da SOTTO soglia - isteresi a
    SOPRA soglia, ignorando quello che succede in mezzo. Senza isteresi la
    coda lenta di un impulso, scendendo attraverso la soglia, la riattraversa
    piu' volte per il rumore, e ogni risalita sembra un impulso nuovo: su
    dati simulati a 1 MHz il conteggio senza isteresi dava il 10% in PIU' del
    vero, anche se il pile-up dovrebbe farlo calare. Una traccia che comincia
    gia' sopra soglia (dentro la coda di un impulso precedente) non conta
    finche' non e' scesa.

    `tratto` = (i0, i1) conta solo i passaggi che cadono fra quei campioni.
    Lo stato dell'isteresi si segue comunque sull'intera traccia: ripartendo
    da zero all'inizio del tratto, un impulso non conterebbe finche' la
    traccia non e' scesa sotto soglia - isteresi, e a MHz di impulsi con code
    di centinaia di ns la traccia comincia spesso dentro una coda. Misurato
    sulla run 0039: il primo mezzo tratto perdeva il 10% dei conteggi a
    1.5 p.e. (il 30% tagliando la traccia prima del filtro).

    Ritorna, per ogni soglia: impulsi (totali, prima meta', seconda meta'),
    eventi con almeno un impulso, campioni sopra soglia.
    """
    n, S = y.shape
    i0, i1 = tratto if tratto else (0, S)
    # passa[:, j] e' il passaggio fra il campione j e j+1: nel tratto se j+1 vi cade
    j0, j1 = max(i0 - 1, 0), i1 - 1
    meta = j0 + (j1 - j0) // 2
    righe = np.arange(n)[:, None]
    pos = np.arange(S, dtype=np.int32)[None, :]
    k = len(soglie)
    tot, prima, seconda, con = (np.zeros(k, dtype=np.int64) for _ in range(4))
    for i, s in enumerate(soglie):
        stato = np.where(y > s, 1, np.where(y < s - isteresi, -1, 0)).astype(np.int8)
        # l'ultimo stato deciso (+1 o -1) a ogni campione, propagato in avanti
        idx = np.where(stato != 0, pos, -1)
        idx = np.maximum.accumulate(idx, axis=1)
        ultimo = np.where(idx >= 0, stato[righe, np.maximum(idx, 0)], 0)
        passa = (ultimo[:, 1:] == 1) & (ultimo[:, :-1] == -1)     # passaggio in t+1
        passa = passa[:, j0:j1]
        tot[i] = np.count_nonzero(passa)
        prima[i] = np.count_nonzero(passa[:, :meta - j0])
        seconda[i] = tot[i] - prima[i]
        con[i] = np.count_nonzero(passa.any(axis=1))
    tutti = np.sort(y[:, i0:i1], axis=None)
    sopra = tutti.size - np.searchsorted(tutti, soglie, "right")
    return tot, prima, seconda, con, sopra


def analizza_canale(x, dt_ns, segno=1, soglie=None, filtro=True, isteresi_sigma=3.0, n_soglie=160,
                    tratto=None):
    """Scala del DCR per un canale. x: (eventi, campioni), conteggi ADC grezzi.

    `tratto` = (i0, i1): si contano solo gli impulsi che cadono fra quei
    campioni, ma filtro, piedistallo e isteresi lavorano sulla traccia intera.
    Tagliare PRIMA del filtro creava all'inizio del tratto un buco di
    conteggi (il 30% a 1.5 p.e. sulla run 0039)."""
    n, S = x.shape
    i0, i1 = tratto if tratto else (0, S)
    sos = butter(LP_ORDINE, LP_MHZ * 1e6, btype="low", fs=1e9 / dt_ns, output="sos") if filtro else None
    # Una prima passata su pochi eventi per il rumore e l'escursione: fissa la
    # griglia delle soglie, uguale per tutti i blocchi.
    def prepara(blocco):
        y = segno * blocco.astype(np.float64)
        if sos is not None:
            y = sosfiltfilt(sos, y, axis=1)
        b, s = piedistallo_dal_basso(y)
        return y - b[:, None], s

    y0, s0 = prepara(x[:min(n, 2000)])
    sigma = float(np.median(s0))
    if soglie is None:
        # Fino al massimo visto, non a un percentile: a DCR basso quasi tutte
        # le finestre sono vuote e un percentile cade sotto il primo
        # fotoelettrone, e allora i picchi non si trovano.
        alto = max(float(y0.max()), 10 * sigma)
        passo = max(sigma / 4.0, 0.25, alto / n_soglie)
        soglie = np.arange(passo, alto + passo, passo)
    soglie = np.asarray(soglie, dtype=np.float64)
    isteresi = isteresi_sigma * sigma

    acc = [np.zeros(len(soglie), dtype=np.int64) for _ in range(5)]
    sigme = []
    for i in range(0, n, BLOCCO):
        y, s = prepara(x[i:i + BLOCCO])
        sigme.append(s)
        for a, v in zip(acc, conta_blocco(y, soglie, isteresi, (i0, i1))):
            a += v
    tot, prima, seconda, con, sopra = acc
    sigma = float(np.median(np.concatenate(sigme)))

    L = (i1 - max(i0 - 1, 0)) - 1 if tratto else S - 1   # passaggi possibili
    T = L * dt_ns * 1e-9                       # s: dove puo' cadere un attraversamento
    m = tot / (n * T)                          # Hz, conteggio diretto
    with np.errstate(divide="ignore", invalid="ignore"):
        tau = np.where(tot > 0, sopra * dt_ns * 1e-9 / tot, np.nan)
        corr = np.where(m * tau < 0.95, m / (1.0 - m * tau), np.nan)
        p0 = 1.0 - con / n
        poisson = np.where((p0 > 0) & (p0 < 1), -np.log(p0) / T, np.where(p0 >= 1, 0.0, np.nan))
        # errore statistico della stima di Poisson: binomiale su P0
        poisson_err = np.where((p0 > 0) & (p0 < 1), np.sqrt((1 - p0) / (n * p0)) / T, np.nan)
    m_err = np.sqrt(tot) / (n * T)
    T2 = np.array([L // 2, L - L // 2]) * dt_ns * 1e-9

    return {
        "eventi": int(n), "campioni": int(S), "tratto": [int(i0), int(i1)], "dt_ns": dt_ns, "finestra_s": T,
        "sigma_rumore": sigma, "isteresi": isteresi, "filtro": ("passa-basso %g MHz" % LP_MHZ) if filtro else "nessuno",
        "soglie": soglie, "attraversamenti": tot, "eventi_con_impulso": con,
        "dcr_conteggio": m, "dcr_conteggio_err": m_err,
        "dcr_tempo_morto": corr, "tau_s": tau,
        "dcr_poisson": poisson, "dcr_poisson_err": poisson_err,
        "dcr_prima_meta": prima / (n * T2[0]), "dcr_seconda_meta": seconda / (n * T2[1]),
    }


def picchi_pe(r, max_pe=4):
    """Ampiezze dei picchi a 1, 2, ... p.e. dalla derivata della scala.

    -dN/ds e' lo spettro delle ampiezze degli impulsi. Si liscia con una
    finestra di circa mezzo sigma di rumore e si cercano i massimi sopra
    3 sigma. Ritorna la lista delle ampiezze, oppure [] se i picchi non si
    vedono (spettro senza struttura: guadagno basso o rumore alto).
    """
    s, N = r["soglie"], r["attraversamenti"].astype(float)
    d = -np.gradient(N, s)
    passo = s[1] - s[0]
    k = max(1, int(round(0.5 * r["sigma_rumore"] / passo)))
    d = np.convolve(d, np.ones(2 * k + 1) / (2 * k + 1), mode="same")
    # La ricerca parte dal primo minimo dopo il rumore: la coda del rumore
    # alle soglie basse e' molto piu' alta dei picchi dei fotoelettroni, e
    # confrontati con lei i picchi sparirebbero (succedeva a 100 kHz).
    ok = np.flatnonzero(s > 3 * r["sigma_rumore"])
    if len(ok) < 5:
        return [], d
    i0 = int(ok[0])
    while i0 + 1 < len(d) and d[i0 + 1] <= d[i0]:
        i0 += 1
    zona = d[i0:]
    if len(zona) < 5 or not np.isfinite(zona).any() or np.nanmax(zona) <= 0:
        return [], d
    prom = 0.05 * float(np.nanmax(zona))
    p, _ = find_peaks(zona, prominence=prom, distance=max(1, int(2 * r["sigma_rumore"] / passo)))
    return [float(s[i0 + j]) for j in p[:max_pe]], d


def soglie_pe(picchi):
    """Soglie a 0.5, 1.5, 2.5 p.e.: meta' del primo picco, poi a meta' fra i picchi."""
    if not picchi:
        return {}
    fuori = {"0.5": picchi[0] / 2.0}
    for k in range(1, len(picchi)):
        fuori["%.1f" % (k + 0.5)] = (picchi[k - 1] + picchi[k]) / 2.0
    # Con due soli picchi visibili (DCR basso: il terzo ha pochi conteggi) la
    # soglia a 2.5 p.e. si mette a mezzo passo oltre il secondo, col passo
    # misurato fra i due.
    if len(picchi) == 2:
        fuori["2.5"] = picchi[1] + (picchi[1] - picchi[0]) / 2.0
    return fuori


def leggi_a(r, chiave, s):
    """Valore di una curva alla soglia s (interpolazione lineare)."""
    return float(np.interp(s, r["soglie"], r[chiave]))


def grafico(risultati, titolo, path_png):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    canali = list(risultati)
    fig, assi = plt.subplots(2, len(canali), figsize=(5.2 * len(canali), 7.2), squeeze=False,
                             sharex="col", gridspec_kw={"height_ratios": [2, 1]})
    for j, ch in enumerate(canali):
        r = risultati[ch]
        s_mv = r["soglie"] * MV_PER_CONTEGGIO
        ax = assi[0][j]
        ax.errorbar(s_mv, r["dcr_conteggio"], yerr=r["dcr_conteggio_err"], fmt="-", lw=1,
                    color="tab:blue", label="counting")
        ax.plot(s_mv, r["dcr_tempo_morto"], "-", lw=1, color="tab:green", label="dead-time corrected")
        ax.plot(s_mv, r["dcr_poisson"], "-", lw=1.4, color="black", label="Poisson (empty windows)")
        ax.plot(s_mv, r["dcr_prima_meta"], ":", lw=0.8, color="tab:orange", label="first half of window")
        ax.plot(s_mv, r["dcr_seconda_meta"], ":", lw=0.8, color="tab:red", label="second half")
        rum = 3 * r["sigma_rumore"] * MV_PER_CONTEGGIO
        for a in (ax, assi[1][j]):
            a.axvspan(0, rum, color="#ddd", alpha=0.6, lw=0)
            for nome, s in r.get("soglie_pe", {}).items():
                a.axvline(s * MV_PER_CONTEGGIO, color="#888", lw=0.7, ls="--")
            a.grid(alpha=0.3)
        for nome, s in r.get("soglie_pe", {}).items():
            ax.text(s * MV_PER_CONTEGGIO, 0.02, " %s p.e." % nome, rotation=90, va="bottom",
                    ha="left", fontsize=7, color="#555", transform=ax.get_xaxis_transform())
        ax.set_yscale("log")
        ax.set_ylabel("dark count rate [Hz]")
        ax.set_title("ch%s  (%d windows, %.2f µs each)" % (ch, r["eventi"], r["finestra_s"] * 1e6),
                     fontsize=10)
        if j == 0:
            ax.legend(fontsize=7, loc="upper right")
        ax2 = assi[1][j]
        ax2.plot(s_mv, r["spettro"], color="tab:purple", lw=1)
        for p in r.get("picchi", []):
            ax2.axvline(p * MV_PER_CONTEGGIO, color="tab:purple", lw=0.6, alpha=0.5)
        # L'asse sui picchi dei fotoelettroni: il picco del rumore alle soglie
        # basse e' dieci volte piu' alto e li schiaccerebbe.
        da = r.get("soglie_pe", {}).get("0.5", 5 * r["sigma_rumore"])
        vis = np.asarray(r["spettro"], dtype=float)[r["soglie"] > da]
        if vis.size and np.isfinite(vis).any() and np.nanmax(vis) > 0:
            ax2.set_ylim(min(0.0, float(np.nanmin(vis))), 1.3 * float(np.nanmax(vis)))
        ax2.set_ylabel("−dN/dthreshold\n(pulse amplitude spectrum)", fontsize=8)
        ax2.set_xlabel("threshold [mV]   (grey: below 3σ of the filtered noise)")
    fig.suptitle(titolo, fontsize=10)
    fig.tight_layout()
    fig.savefig(path_png, dpi=110)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--canali", type=int, nargs="+", help="canali da analizzare (di serie tutti)")
    ap.add_argument("-n", "--max-eventi", type=int, default=None)
    ap.add_argument("--segno", type=int, choices=(1, -1), default=None,
                    help="verso degli impulsi; di serie dal log della run (DCOffset), se no +1")
    ap.add_argument("--grezzo", action="store_true", help="senza il passa-basso a 50 MHz")
    ap.add_argument("--scarta-coda", type=int, default=SCARTA_CODA)
    ap.add_argument("--intervallo-ns", type=float, nargs=2, metavar=("DA", "A"), default=None,
                    help="analizza solo questo tratto della finestra, in ns dall'inizio. Col "
                         "trigger software serve a tenersi lontani dall'istante del trigger, dove "
                         "il segnale grezzo ha raffiche a ~176 MHz (CLAUDE.md, 2026-10-26), e "
                         "dai primi ~85 ns, piu' rumorosi anche dopo il filtro")
    ap.add_argument("--forza", action="store_true",
                    help="analizza anche un file che non e' a solo trigger software")
    ap.add_argument("--json", default=None, help="di serie measurements/dcr_<run>.json")
    ap.add_argument("--png", default=None, help="di serie accanto al JSON")
    a = ap.parse_args()

    hdr, data = daqio.load(a.file, max_events=a.max_eventi, channels=a.canali)
    modo = str(hdr.get("TriggerMode", "?"))
    if modo != "Software":
        msg = ("il file dichiara TriggerMode = %r, non 'Software': le finestre non sono cieche "
               "e il DCR che ne esce e' falsato." % modo)
        if not a.forza:
            sys.exit("Errore: " + msg + " (--forza per analizzarlo lo stesso)")
        print("ATTENZIONE: " + msg)
    if a.scarta_coda:
        data = data[:, :, :max(data.shape[2] - a.scarta_coda, 10)]
    dt_ns = float(hdr.get("SamplingTime", 1e-9)) * 1e9
    tratto = None
    if a.intervallo_ns:
        # Un tratto CONTINUO, non una finestra con un buco. Non si taglia la
        # traccia: lo dice analizza_canale perche'.
        i0 = max(0, int(round(a.intervallo_ns[0] / dt_ns)))
        i1 = min(data.shape[2], int(round(a.intervallo_ns[1] / dt_ns)))
        if i1 - i0 < 20:
            sys.exit("Errore: l'intervallo %s ns lascia %d campioni." % (a.intervallo_ns, i1 - i0))
        tratto = (i0, i1)
    canali = [int(c) for c in hdr["ChannelList"]]

    run = efficienza.nome_run(a.file)
    pol = efficienza.polarita_dal_log(os.path.splitext(a.file.replace(".h5.gz", ".h5"))[0] + ".log")
    risultati = {}
    print("%s: %d finestre da %.2f µs, campionamento %.3g ns%s" % (
        run, data.shape[0], (data.shape[2] - 1) * dt_ns / 1000, dt_ns,
        ", contati solo gli impulsi fra %.0f e %.0f ns" % (tratto[0] * dt_ns, tratto[1] * dt_ns)
        if tratto else ""))
    print("%4s %4s %7s %8s  %-30s %-30s %-30s %6s" % (
        "ch", "pol", "rumore", "1 p.e.", "DCR 0.5 p.e. [kHz]", "DCR 1.5 p.e. [kHz]",
        "DCR 2.5 p.e. [kHz]", "xtalk"))
    for j, ch in enumerate(canali):
        if a.segno is not None:
            segno = a.segno
        elif isinstance(pol, dict):
            segno = pol.get(ch, 1)
        elif pol in (1, -1):
            segno = pol
        else:
            segno = 1
        r = analizza_canale(data[:, j, :], dt_ns, segno=segno, filtro=not a.grezzo, tratto=tratto)
        picchi, spettro = picchi_pe(r)
        r["picchi"], r["spettro"], r["soglie_pe"], r["segno"] = picchi, spettro, soglie_pe(picchi), segno
        risultati[ch] = r
        celle = []
        for k in ("0.5", "1.5", "2.5"):
            s = r["soglie_pe"].get(k)
            if s is None:
                celle.append("%-30s" % "-")
                continue
            p = leggi_a(r, "dcr_poisson", s)
            c = leggi_a(r, "dcr_conteggio", s)
            t = leggi_a(r, "dcr_tempo_morto", s)
            celle.append("%-30s" % ("P %.4g  c %.4g  tm %.4g" % (p / 1e3, c / 1e3, t / 1e3)))
        xt = "-"
        if "1.5" in r["soglie_pe"]:
            d05 = leggi_a(r, "dcr_poisson", r["soglie_pe"]["0.5"])
            d15 = leggi_a(r, "dcr_poisson", r["soglie_pe"]["1.5"])
            if d05 > 0 and math.isfinite(d05) and math.isfinite(d15):
                xt = "%.3f" % (d15 / d05)
                r["crosstalk"] = d15 / d05
        print("%4d %4s %5.2f mV %6.2f mV  %s %6s" % (
            ch, "+" if segno > 0 else "-", r["sigma_rumore"] * MV_PER_CONTEGGIO,
            (picchi[0] if picchi else float("nan")) * MV_PER_CONTEGGIO, " ".join(celle), xt))
    print("P = Poisson dalle finestre vuote, c = conteggio diretto, tm = conteggio corretto per il "
          "tempo morto. xtalk = DCR(1.5)/DCR(0.5) dalla stima di Poisson.")
    print("Controllo del campione cieco: DCR nella prima e nella seconda meta' della finestra "
          "(curve punteggiate nel grafico) devono coincidere.")

    os.makedirs(os.path.join(ROOT, "measurements"), exist_ok=True)
    pj = a.json or os.path.join(ROOT, "measurements", "dcr_%s.json" % run)
    pp = a.png or os.path.splitext(pj)[0] + ".png"

    def serial(v):
        if isinstance(v, np.ndarray):
            return [None if not np.isfinite(x) else float(x) for x in v.astype(float)]
        return v
    with open(pj, "w") as f:
        json.dump({"file": os.path.abspath(a.file), "run": run, "trigger_mode": modo,
                   "scarta_coda": a.scarta_coda, "intervallo_ns": a.intervallo_ns,
                   "mv_per_conteggio": MV_PER_CONTEGGIO,
                   "canali": {str(ch): {k: serial(v) for k, v in r.items()}
                              for ch, r in risultati.items()}}, f, indent=1)
    grafico(risultati, "%s — dark count rate vs threshold (software trigger, blind windows)" % run, pp)
    print("scritti %s e %s" % (pj, pp))


if __name__ == "__main__":
    main()
