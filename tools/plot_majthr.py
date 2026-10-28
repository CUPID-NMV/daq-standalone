#!/usr/bin/env python3
"""
Confronto delle calibrazioni della somma S del V812 (soglia di maggioranza).

    python3 tools/plot_majthr.py [--png measurements/v812_majthr_confronto.png]

Rilegge gli scan del registro 0x48 in measurements/v812_majthr_*.json (rate e
molteplicita' per passo) e disegna:
  sopra  il rate contro la soglia scritta nel registro, uno scan per data;
  sotto  la somma S contro il numero di canali: gli intervalli misurati dei
         bordi e la retta che la DAQ usa per mettere la soglia.

Il bordo "k -> k+1 canali" e' la soglia oltre la quale gli eventi con k canali
non passano piu': e' quindi la somma di k canali, S(k). Lo si legge dove la
molteplicita' minima degli eventi registrati sale, non dal rate da solo.
"""
import argparse
import json
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

QUI = os.path.dirname(os.path.abspath(__file__))
MIS = os.path.join(QUI, "..", "measurements")

# (file, etichetta, colore, retta mV/canale, spostamento mV)
SCAN = [("v812_majthr_20261027.json", "27/10, output S open", "#2a6fdb", 55.0, 17.0),
        ("v812_majthr_20261028.json", "28/10, output S terminated (50 Ω)", "#e8710a", 52.0, 11.0)]


def bordi(passi):
    """Per k = 1, 2, 3: l'ultimo passo in cui gli eventi con al piu' k canali
    sono ancora la maggioranza, e il primo in cui non lo sono piu'.

    A meta' e non al primo evento: subito oltre il bordo passa ancora il
    13-15% di eventi con k canali, perche' la corrente di un canale non e'
    identica per tutti (a 72 mV il 27/10, a 64 mV il 28/10)."""
    fuori = {}
    for k in (1, 2, 3):
        dentro = [p["soglia_mV"] for p in passi
                  if sum(p["molteplicita_1_2_3"][:k]) > 0.5 * max(1, sum(p["molteplicita_1_2_3"]))]
        sopra = [p["soglia_mV"] for p in passi if p["soglia_mV"] > max(dentro, default=0)]
        if dentro and sopra:
            fuori[k] = (max(dentro), min(sopra))
    return fuori


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--png", default=os.path.join(MIS, "v812_majthr_confronto.png"))
    a = ap.parse_args()

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8.5, 8.6), height_ratios=[1.15, 1])

    for nome, etich, col, m, q in SCAN:
        passi = json.load(open(os.path.join(MIS, nome)))["passi"]
        x = np.array([p["soglia_mV"] for p in passi], float)
        r = np.array([p["rate_hz"] for p in passi], float)
        # Rate zero non si disegna in scala log: va sul pavimento con un
        # triangolo, perche' "nessun evento" e' una misura, non un buco.
        # Pavimenti diversi per i due scan: hanno gli stessi passi, e i
        # triangoli di uno coprivano quelli dell'altro.
        pav = 0.03 if col == SCAN[0][2] else 0.05
        ax1.plot(x, np.where(r > 0, r, np.nan), "-o", color=col, lw=2, ms=4.5, label=etich)
        ax1.plot(x[r == 0], np.full((r == 0).sum(), pav), "v", color=col, ms=6, ls="none")

        b = bordi(passi)
        ks = sorted(b)
        lo = np.array([b[k][0] for k in ks]); hi = np.array([b[k][1] for k in ks])
        off = -0.06 if col == SCAN[0][2] else 0.06
        ax2.errorbar(np.array(ks) + off, (lo + hi) / 2, yerr=(hi - lo) / 2, fmt="o", color=col,
                     ms=6, capsize=5, lw=2, label="%s: measured edges" % etich.split(",")[0])
        kk = np.linspace(0.6, 3.4, 10)
        ax2.plot(kk, m * kk + q, color=col, lw=1.6,
                 label="S = %.0f mV × k + %.0f mV" % (m, q))
        ke = np.linspace(3.4, 5.4, 10)
        ax2.plot(ke, m * ke + q, color=col, lw=1.6, ls=":")

    # Le zone della soglia sul grafico del rate: cosa passa, per lo scan nuovo.
    for x0, x1, testo in [(30, 62, "≥ 1 channel"), (62, 114, "≥ 2 channels"),
                          (114, 166, "3 channels"), (166, 285, "nothing passes")]:
        ax1.text((x0 + x1) / 2, 1500, testo, ha="center", va="center", fontsize=8.5, color="#555")
    for xv in (62, 114, 166):
        ax1.axvline(xv, color="#e8710a", lw=0.8, ls="--", alpha=0.5)
    ax1.set_yscale("log")
    ax1.set_ylim(0.02, 4000)
    ax1.set_xlim(25, 285)
    ax1.set_xlabel("majority threshold written in register 0x48 [mV]  (4 mV per count)")
    ax1.set_ylabel("trigger rate [Hz]")
    ax1.set_title("V812 majority: rate vs threshold, three SiPM inputs at 30 mV",
                  fontsize=11, loc="left")
    ax1.text(0.99, 0.14,"▼ = no events in the step", transform=ax1.transAxes,
             ha="right", va="bottom", fontsize=8, color="#555")
    ax1.grid(alpha=0.25, which="both")
    ax1.legend(loc="upper right", bbox_to_anchor=(1, 0.88), fontsize=9, frameon=False)

    k = np.arange(1, 6)
    # La somma NOMINALE del manuale (50 mV per canale), non i suoi livelli di
    # soglia 50N - 25: quelli sono soglie, e accanto alle somme misurate
    # sembrerebbero un terzo modulo.
    ax2.plot(k, 50 * k, "--", color="#888", lw=1.2,
             label="manual: 50 mV per channel (±20%)")
    ax2.set_xticks(k)
    ax2.set_xlim(0.5, 5.5)
    ax2.set_ylim(0, 300)
    ax2.set_xlabel("channels over threshold, k")
    ax2.set_ylabel("sum S [mV]")
    ax2.set_title("Sum S per number of channels (dotted: extrapolated, not measured)",
                  fontsize=11, loc="left")
    ax2.grid(alpha=0.25)
    ax2.legend(fontsize=8.5, frameon=False, loc="upper left")

    fig.tight_layout()
    fig.savefig(a.png, dpi=130)
    print("scritto", a.png)


if __name__ == "__main__":
    main()
