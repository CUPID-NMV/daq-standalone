#!/usr/bin/env python3
"""
Quanto segnale vede il CFD rispetto al digitizer, dai due scan in soglia.

    python3 tools/rapporto_catene.py \\
        measurements/calib_selftrigger_20261013_104413.json \\
        plots/scan_v812_20261013_092419.json

Le due catene partono dallo stesso rivelatore e finiscono in due strumenti
diversi, con in mezzo sdoppiatori, amplificatori e attenuatori che non stanno
in nessun file: stanno nei cavi. Il rapporto fra le ampiezze ai due ingressi e'
quello che serve per dire, su un asse, "offset 300 equivale a tot mV AL CFD".

Tutti e due gli scan misurano la stessa cosa -- il rate sopra una soglia, cioe'
la cumulativa complementare delle ampiezze -- quindi uno e' l'altro riscalato
in ascissa. Si cercano DUE parametri, non uno:

  g  il rapporto delle ampiezze: una soglia V al CFD corrisponde a V/g al
     digitizer;
  N  la normalizzazione in rate, perche' i due scan sono stati presi a un'ora
     di distanza e il rate della sorgente non e' lo stesso: 534 Hz di plateau
     alle 9:39 contro un self-trigger che a soglia minima ne vede 389.

Cercare il solo g, cioe' sovrapporre le curve a occhio, assorbe la differenza
di normalizzazione dentro la scala e da' un g sbagliato. E' il motivo per cui
questo numero va misurato invece che guardato.

ATTENZIONE: il modo definitivo di misurare g e' il banco -- un impulso, e
l'oscilloscopio prima su un ingresso e poi sull'altro. Qui si ricava dai dati
che ci sono gia', con l'ipotesi che fra i due scan sia cambiato solo il rate
della sorgente e non la sua distribuzione di ampiezze.
"""
import argparse
import json
import sys

import numpy as np


def curva_self(d):
    """(soglia al digitizer in mV, rate) dal JSON della calibrazione."""
    p = [q for q in d["punti"] if q["frazione"] < 0.98]
    x = np.array([q["soglia_mv"] for q in p], float)
    y = np.array([q["rate"] for q in p], float)
    o = np.argsort(x)
    return x[o], y[o]


def curva_cfd(d):
    """(soglia al CFD in mV, rate) dal JSON dello scan del V812."""
    p = [q for q in d["punti"] if q.get("rate")]
    x = np.array([q["soglia_mv"] for q in p], float)
    y = np.array([q["rate"] for q in p], float)
    o = np.argsort(x)
    return x[o], y[o]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("calib", help="il JSON di calibra_selftrigger.py")
    ap.add_argument("cfd", help="il JSON di uno scan del V812")
    ap.add_argument("-o", "--out", default=None)
    a = ap.parse_args()

    dc = json.load(open(a.calib))
    ds = json.load(open(a.cfd))
    xs, ys = curva_self(dc)
    xc, yc = curva_cfd(ds)
    mv_off = dc["fit"]["pendenza_mv_per_offset"]

    print("self-trigger : %d punti, soglia %.1f-%.1f mV al digitizer, rate %.0f-%.0f Hz"
          % (len(xs), xs.min(), xs.max(), ys.max(), ys.min()))
    print("CFD          : %d punti, soglia %.0f-%.0f mV al CFD, rate %.0f-%.0f Hz"
          % (len(xc), xc.min(), xc.max(), yc.max(), yc.min()))
    print("calibrazione : %.4f mV/offset all'ingresso del digitizer" % mv_off)
    print()

    # Si interpola la curva del self-trigger in log(rate): un rate e' una
    # quantita' positiva che cambia di un ordine di grandezza sullo scan, e
    # interpolarlo linearmente pesa solo i punti ad alto rate.
    def rate_self(t):
        return np.exp(np.interp(t, xs, np.log(ys)))

    # Per ogni g si usano solo i punti del CFD la cui soglia riportata al
    # digitizer cade DENTRO l'intervallo misurato dal self-trigger: fuori si
    # estrapolerebbe, e l'estrapolazione deciderebbe il fit.
    def scarto(g):
        t = xc / g
        buoni = (t >= xs.min()) & (t <= xs.max())
        if buoni.sum() < 4:
            return np.inf, buoni.sum(), np.nan
        prev = rate_self(t[buoni])
        oss = yc[buoni]
        # N si ricava in forma chiusa: in log, e' la media delle differenze.
        logN = float(np.mean(np.log(oss) - np.log(prev)))
        res = np.log(oss) - (np.log(prev) + logN)
        return float(np.sqrt(np.mean(res ** 2))), int(buoni.sum()), float(np.exp(logN))

    griglia = np.linspace(0.20, 1.20, 2001)
    valori = [scarto(g) for g in griglia]
    rms = np.array([v[0] for v in valori])
    k = int(np.argmin(rms))
    g_best, rms_best, n_best, N_best = griglia[k], rms[k], valori[k][1], valori[k][2]

    # L'errore: quanto si puo' muovere g prima che lo scarto peggiori di
    # sqrt(1 + 1/n), cioe' di una deviazione standard sui residui.
    soglia_err = rms_best * np.sqrt(1.0 + 1.0 / max(n_best, 2))
    dentro = griglia[rms <= soglia_err]
    g_lo, g_hi = (dentro.min(), dentro.max()) if len(dentro) else (np.nan, np.nan)

    print("rapporto delle ampiezze CFD/digitizer : g = %.3f  (%.3f - %.3f)"
          % (g_best, g_lo, g_hi))
    print("  punti del CFD usati                 : %d" % n_best)
    print("  normalizzazione in rate             : %.3f  (i due scan non hanno"
          " lo stesso rate di sorgente)" % N_best)
    print("  residui                             : %.1f%% in rate" % (100 * rms_best))
    print()
    print("quindi, con QUESTO cablaggio:")
    print("  all'ingresso del digitizer : %.3f mV per unita' di offset" % mv_off)
    print("  all'ingresso del CFD       : %.3f mV per unita' di offset  (%.3f-%.3f)"
          % (mv_off * g_best, mv_off * g_lo, mv_off * g_hi))
    print()
    print("  il CFD vede il %.0f%% dell'ampiezza che vede il digitizer."
          % (100 * g_best))
    print("  Questo numero NON e' una proprieta' della scheda: cambia con i cavi.")

    if a.out:
        json.dump({
            "calib": a.calib, "cfd": a.cfd,
            "mv_per_offset_digitizer": mv_off,
            "g": g_best, "g_min": float(g_lo), "g_max": float(g_hi),
            "mv_per_offset_cfd": mv_off * g_best,
            "normalizzazione_rate": N_best,
            "residui_rms_rate": rms_best,
            "punti_usati": n_best,
        }, open(a.out, "w"), indent=2)
        print("\ndati grezzi in %s" % a.out)


if __name__ == "__main__":
    main()
