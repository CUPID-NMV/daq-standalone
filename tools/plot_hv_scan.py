#!/usr/bin/env python3
"""
Rate di trigger contro soglia, una curva per ogni valore del driver HV.

Legge una misura salvata in measurements/ e produce il grafico. I punti a zero
conteggi sono limiti superiori e vanno disegnati come tali: sono informazione,
non rate nulli.

    python3 tools/plot_hv_scan.py measurements/hv_scan_20260923.json
"""

import json
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "measurements", "hv_scan_20260923.json")
    m = json.load(open(src))
    pts = m["punti"]

    fig, ax = plt.subplots(figsize=(8.2, 5.4))
    colors = plt.cm.viridis(np.linspace(0.08, 0.88, len(pts)))

    for p, col in zip(pts, colors):
        d = np.array(p["distanze"], dtype=float)
        r = np.array(p["rate"], dtype=float)
        lim = np.array(p["limite"], dtype=bool)

        # Una curva puo' chiedere uno stile proprio, per esempio per distinguere
        # una misura di controllo dalle altre
        st_ = p.get("stile", {})
        col = st_.get("colore", col)
        ls  = st_.get("tratto", "-")
        mk  = st_.get("marker", "o")

        ax.plot(d[~lim], r[~lim], marker=mk, ls=ls, color=col, lw=1.8, ms=6,
                label=p.get("etichetta", f"HV {p['hv']}"))
        if lim.any():
            # limiti superiori: triangolo verso il basso, linea tratteggiata
            ax.plot(d[lim], r[lim], "v", color=col, ms=7, mfc="none")
            order = np.argsort(d)
            ax.plot(d[order], r[order], ":", color=col, lw=1, alpha=.5)

    ax.set_yscale("log")
    ax.set_xlabel("distanza baseline − soglia  [conteggi Transparent Mode]")
    ax.set_ylabel("rate di trigger  [Hz]")
    ax.set_title("Rate contro soglia, al variare del guadagno dei PMT", fontsize=12)
    ax.grid(alpha=.3, which="both")
    ax.invert_xaxis()                     # soglia piu' bassa verso destra
    ax.legend(title="driver HV", fontsize=9)

    for a in m.get("annotazioni", []):
        ax.annotate(a["testo"], xy=tuple(a["xy"]), xytext=tuple(a["xytext"]),
                    fontsize=9, color="#444",
                    arrowprops=dict(arrowstyle="->", color="#999", lw=1))
    ax.text(0.98, 0.03, "▽  limite superiore (zero conteggi nel tempo di misura)",
            transform=ax.transAxes, fontsize=8, color="#666", ha="right")
    ax.set_ylim(0.02, 3000)

    name = os.path.splitext(os.path.basename(src))[0]
    out = os.path.join(ROOT, "plots", name + ".png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    print("grafico:", out)


if __name__ == "__main__":
    main()
