#!/usr/bin/env python3
"""Grafico di uno scan in soglia, del self-trigger o del CFD, o dei due insieme.

    python3 tools/plot_scan.py plots/noise_scan_20261001_120000.json
    python3 tools/plot_scan.py plots/noise_scan_*.json plots/scan_v812_*.json

Sovrapporre i due e' il motivo per cui questo script esiste: il self-trigger e
il V812 discriminano lo stesso segnale in due modi diversi, e l'unico modo
onesto di confrontarli e' mettere entrambi i rate sulla stessa scala in
millivolt. Per il self-trigger la soglia e' in conteggi di offset, e la
conversione in millivolt dipende dalla frequenza di campionamento: si ricava
dal nome della run registrato nel JSON.
"""

import argparse
import json
import os
import sys
import time

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# mV per unita' di offset del self-trigger, corretti per la larghezza vera
# degli impulsi dei PMT (FWHM 1.80 ns, misurata a 2.5 GS/s il 2026-09-30).
# Vedi measurements/larghezza_pmt_20260930.json e CLAUDE.md.
MV_PER_OFFSET = {"2.5Gs": 3.5, "1Gs": 2.7}

# Il colore distingue le SERIE, non lo strumento: due scan dello stesso
# modulo -- per esempio un canale per volta -- devono avere colori diversi, se
# no non si distinguono. A dire quale strumento sia ci pensano il marker e il
# tratto, che restano fissi per tipo.
#
# Ordine categorico fisso, mai ciclato: passa i controlli di separazione sia a
# vista normale sia col daltonismo. La separazione minima sta nella fascia che
# richiede una codifica secondaria, e c'e': marker diverso per strumento, piu'
# la legenda, che per questi grafici e' sempre presente.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
           "#e87ba4", "#008300", "#4a3aa7", "#e34948"]

MARKER = {"v1742": "o", "v812": "s"}
TRATTO = {"v1742": "-", "v812": "--"}
INCHIOSTRO = "#52514e"


def _canali(c):
    """I canali come si leggono in una legenda, non come li stampa Python."""
    if not c:
        return "?"
    if not isinstance(c, (list, tuple)):
        return str(c)
    return ", ".join(str(x) for x in c)


def frequenza(nome_file):
    """Frequenza di campionamento dedotta dal nome della run."""
    for tag in MV_PER_OFFSET:
        if "_%s_" % tag in nome_file:
            return tag
    return None


def leggi(path, mv_per_offset_forzato):
    """Normalizza i due formati in (tipo, soglie_mv, rate, limite, nota)."""
    d = json.load(open(path))
    punti = d.get("punti", [])
    if not punti:
        raise SystemExit("%s non contiene punti." % path)

    if d.get("tipo") == "v812":
        x = np.array([p["soglia_mv"] for p in punti], dtype=float)
        y = np.array([p["rate"] for p in punti], dtype=float)
        lim = np.array([p["eventi"] == 0 for p in punti])
        nota = "V812 CFD   ch %s" % _canali(d.get("canali"))
        return "v812", x, y, lim, nota, d

    # formato di noise_scan.py: soglia in conteggi di offset
    tag = frequenza(d.get("file", ""))
    k = mv_per_offset_forzato or MV_PER_OFFSET.get(tag)
    if k is None:
        raise SystemExit(
            "Non riesco a dedurre la frequenza di campionamento da '%s', quindi "
            "non so convertire gli offset in millivolt.\n"
            "Passa --mv-per-offset con il valore giusto." % d.get("file", "?"))
    x = np.array([p["distanza"] for p in punti], dtype=float) * k
    y = np.array([max(p["rate"], 0.0) for p in punti], dtype=float)
    lim = np.array([p.get("conteggi", 1) == 0 for p in punti])
    nota = "V1742 self-trigger   ch %s   (%.1f mV/offset at %s)" % (
        _canali(d.get("canali")), k, (tag or "?").replace("Gs", " GS/s"))
    return "v1742", x, y, lim, nota, d


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json", nargs="+", help="uno o piu' file di scan")
    ap.add_argument("--mv-per-offset", type=float, default=None,
                    help="forza la conversione offset -> mV del self-trigger")
    ap.add_argument("-o", "--out", default=None, help="file PNG di uscita")
    args = ap.parse_args()

    fig, ax = plt.subplots(figsize=(7.4, 4.8))
    visti = []

    if len(args.json) > len(PALETTE):
        raise SystemExit(
            "%d scan in un grafico solo: i colori distinguibili sono %d.\n"
            "Oltre, le curve non si distinguono piu' e il grafico smette di dire "
            "qualcosa. Falli in due figure." % (len(args.json), len(PALETTE)))

    # Si leggono tutti prima di disegnare: due scan della stessa cosa darebbero
    # la stessa etichetta, e una legenda con due voci identiche non distingue
    # niente. Quando succede si aggiunge l'ora dello scan.
    serie = [leggi(path, args.mv_per_offset) for path in args.json]
    note = [s[4] for s in serie]
    for i, s in enumerate(serie):
        if note.count(s[4]) > 1:
            quando = (s[5].get("quando") or "")[5:16].replace("-", "/")
            serie[i] = s[:4] + (s[4] + ("   [%s]" % quando if quando else "   [%d]" % (i + 1)),) + s[5:]

    for n, (tipo, x, y, lim, nota, d) in enumerate(serie):
        col, mk, ls = PALETTE[n], MARKER[tipo], TRATTO[tipo]
        visti.append(tipo)

        # I punti stanno nel JSON nell'ordine in cui sono stati misurati, che
        # non e' detto sia crescente in soglia: gli scan a volte ripetono un
        # punto in fondo per verificare la riproducibilita'. Senza riordinare,
        # la spezzata torna indietro e sembra un secondo ramo della curva.
        ordine = np.argsort(x)
        x, y, lim = x[ordine], y[ordine], lim[ordine]

        vis = ~lim
        if vis.any():
            ax.semilogy(x[vis], np.maximum(y[vis], 1e-3), marker=mk, ls=ls, ms=8,
                        lw=2.0, color=col, mec="white", mew=1.2, label=nota)
        if lim.any():
            # Zero eventi non e' rate zero: su scala logaritmica sarebbe
            # invisibile, e spacciarlo per una misura sarebbe peggio.
            soffitto = 1.0 / d.get("secondi_per_punto", 1.0)
            ax.semilogy(x[lim], [soffitto] * lim.sum(), marker="v", ls="none",
                        ms=9, color=col, mec="white", mew=1.2, alpha=.8,
                        label="upper limit, no events (%s)" % nota.split("  ")[0])

    ax.set_xlabel("threshold  [mV at the detector input]")
    ax.set_ylabel("trigger rate  [Hz]")
    ax.set_title("Threshold scan", fontsize=11)
    ax.grid(alpha=.3, which="both")
    ax.legend(fontsize=8.5)

    if "v1742" in visti and "v812" in visti:
        # A meta' altezza, a destra. Non e' una scelta a occhio: il rate cala
        # con la soglia, quindi la parte destra sotto le curve e' vuota per
        # costruzione. In basso invece ci finiscono i limiti superiori, che
        # occupano tutta la riga.
        ax.annotate("Both discriminators see the same signal.\n"
                    "A gap at equal threshold is efficiency,\n"
                    "not calibration.",
                    xy=(.98, .42), xycoords="axes fraction", fontsize=8,
                    ha="right", va="center", color=INCHIOSTRO,
                    bbox=dict(fc="white", ec="#ddd", alpha=.9))

    out = args.out or os.path.join(ROOT, "plots",
                                   "scan_%s.png" % time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)
    print("grafico: %s" % out)


if __name__ == "__main__":
    main()
