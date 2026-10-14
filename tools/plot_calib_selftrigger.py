#!/usr/bin/env python3
"""
Il grafico della calibrazione del self-trigger misurata dal digitizer stesso.

    python3 tools/plot_calib_selftrigger.py measurements/calib_selftrigger_*.json

I numeri li rilegge dal JSON di `calibra_selftrigger.py`: il grafico si rifa',
la misura no.

Il grafico mostra due stimatori che non condividono ipotesi e la zona
PROIBITA: sotto 0.244 mV per unita' di offset la calibrazione vorrebbe dire
che il Transparent Mode amplifica, e quello e' un percorso passivo. Serve a
vedere a colpo d'occhio perche' lo 0.2 ottenuto sovrapponendo lo scan del CFD
non puo' essere la calibrazione del self-trigger.
"""
import argparse
import json
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                          # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BLU = "#1f6fb4"        # mappatura dei quantili
ARANCIO = "#d95f02"    # bordo basso
GRIGIO = "#6b6b6b"


def retta(x, y):
    """(pendenza, intercetta, errore sulla pendenza, rms dei residui)."""
    p, i = np.polyfit(x, y, 1)
    res = y - (p * x + i)
    s = float(np.sqrt(np.sum(res ** 2) / max(len(x) - 2, 1)))
    e = s / np.sqrt(np.sum((x - x.mean()) ** 2))
    return p, i, e, s


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--cfd", type=float, default=0.2,
                    help="mV/offset ottenuto sovrapponendo lo scan del CFD (0 = non mostrarlo)")
    a = ap.parse_args()

    d = json.load(open(a.json))
    passo = d["mv_per_conteggio"]

    # Metodo 1: si scarta il punto di riferimento, dove la frazione e' 1 per
    # costruzione e il quantile cade sul bordo del campione.
    q = [p for p in d["punti"] if p["frazione"] < 0.98]
    x1 = np.array([p["offset"] for p in q], float)
    y1 = np.array([p["soglia_mv"] for p in q], float)
    p1, i1, e1, s1 = retta(x1, y1)

    v = d.get("verifica_bordo_basso") or []
    x2 = np.array([p["offset"] for p in v], float)
    y2 = np.array([p["q1_mv"] for p in v], float)
    hanno_2 = len(x2) >= 3
    if hanno_2:
        p2, i2, e2, s2 = retta(x2, y2)

    fig, (ax, bx) = plt.subplots(
        2, 1, figsize=(8.2, 7.4), sharex=True,
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.08})

    xx = np.linspace(0, max(x1.max(), x2.max() if hanno_2 else 0) * 1.05, 100)

    # La zona proibita per prima, cosi' sta sotto a tutto il resto.
    ax.fill_between(xx, 0, passo * xx, color=GRIGIO, alpha=0.13, lw=0)
    ax.plot(xx, passo * xx, color=GRIGIO, lw=1.2, ls=":")
    ax.text(xx[-1] * 0.985, passo * xx[-1] * 0.62,
            "impossible: the Transparent Mode\nis passive and can only attenuate",
            ha="right", va="top", fontsize=8.5, color=GRIGIO, style="italic")
    ax.text(xx[-1] * 0.985, passo * xx[-1] * 1.03,
            "0.244 mV/offset  (no attenuation)",
            ha="right", va="bottom", fontsize=8.5, color=GRIGIO)

    if a.cfd:
        ax.plot(xx, a.cfd * xx, color="#8c564b", lw=1.6, ls="--")
        ax.text(xx[-1] * 0.985, a.cfd * xx[-1] * 0.93,
                "%.2f mV/offset from overlaying the CFD scan" % a.cfd,
                ha="right", va="top", fontsize=9, color="#8c564b")

    ax.plot(xx, p1 * xx + i1, color=BLU, lw=1.4, alpha=0.8)
    ax.plot(x1, y1, "o", ms=8, color=BLU, mec="white", mew=1.2, zorder=5,
            label="quantile mapping of the step rates:  %.4f ± %.4f mV/offset"
                  % (p1, e1))
    if hanno_2:
        ax.plot(xx, p2 * xx + i2, color=ARANCIO, lw=1.4, alpha=0.8)
        ax.plot(x2, y2, "s", ms=7, color=ARANCIO, mec="white", mew=1.2, zorder=5,
                label="low edge of each step (1%% quantile):  %.4f ± %.4f mV/offset"
                      % (p2, e2))

    ax.set_ylabel("threshold at the digitizer input  [mV]")
    ax.set_xlim(0, xx[-1])
    ax.set_ylim(0, max(y1.max(), y2.max() if hanno_2 else 0) * 1.12)
    ax.grid(alpha=0.25)
    ax.legend(loc="upper left", fontsize=9, framealpha=0.95)
    ax.set_title("V1742 self-trigger threshold, calibrated by the digitizer itself\n"
                 "%s  ·  ch %s  ·  %s ns FWHM pulses"
                 % (d["run"], ", ".join(str(c) for c in d["canali"]),
                    d.get("larghezza_fwhm_ns")), fontsize=11)

    # I residui: e' li' che si vede se una retta basta davvero.
    bx.axhline(0, color=GRIGIO, lw=1)
    bx.plot(x1, y1 - (p1 * x1 + i1), "o", ms=7, color=BLU, mec="white", mew=1.0)
    if hanno_2:
        bx.plot(x2, y2 - (p2 * x2 + i2), "s", ms=6, color=ARANCIO,
                mec="white", mew=1.0)
    bx.set_xlabel("self-trigger threshold  [offset units]")
    bx.set_ylabel("residual [mV]")
    bx.grid(alpha=0.25)

    nota = ("attenuation %.2f  ·  intercepts %+.1f and %+.1f mV, i.e. consistent "
            "with zero" % (p1 / passo, i1, i2 if hanno_2 else float("nan"))
            if hanno_2 else "attenuation %.2f" % (p1 / passo))
    fig.text(0.5, 0.012, nota, ha="center", fontsize=9, color="#333333")

    out = a.out or os.path.join(ROOT, "plots", os.path.basename(a.json)
                                .replace(".json", ".png"))
    fig.savefig(out, dpi=140, bbox_inches="tight")
    print(out)


if __name__ == "__main__":
    main()
