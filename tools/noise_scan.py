#!/usr/bin/env python3
"""
Pavimento di rumore del self-trigger: rate di trigger in funzione dell'offset,
con il segnale spento.

Senza segnale gli unici trigger vengono dal rumore, quindi la misura non
dipende ne' dallo spettro delle ampiezze ne' dalla stabilita' della sorgente:
e' la curva pulita che lo scan in efficienza non riusciva a dare.

Il rate atteso e' la coda gaussiana del rumore campionato dall'ADC in
Transparent Mode:

    R(offset) = f_ADC * n_canali * 0.5 * erfc( offset / (sigma * sqrt2) )

con sigma misurato dal dump in Transparent Mode. Confrontare misura e
previsione verifica il modello di rumore, e quindi tutte le soglie espresse
in unita' di sigma.

Serve una run gia' avviata con SelfTrigger = true E IL SEGNALE SPENTO
(alta tensione dei PMT giu', o guadagno al minimo).

    python3 tools/noise_scan.py --offsets 5 4.5 4 3.5 3 2.5 --sigma 0.72
"""

import argparse
import glob
import json
import math
import os
import sys
import time

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daqio import load

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F_ADC = 30e6          # campionamento dell'ADC in Transparent Mode, ~30 MHz


def status(data_dir):
    with open(os.path.join(data_dir, "live-status.json")) as f:
        return json.load(f)


def n_events(path):
    hdr, _ = load(path, last=1, live=True)
    return int(hdr["NEventsInFile"])


def set_offset(data_dir, channels, offset, timeout=15):
    with open(os.path.join(data_dir, "live-threshold.txt"), "w") as f:
        for ch in channels:
            f.write(f"{ch} {offset}\n")
    t0 = time.time()
    while time.time() - t0 < timeout:
        applied = {int(c["ch"]): c["offset"] for c in status(data_dir)["channels"]}
        if all(abs(applied.get(ch, -1) - offset) < 1e-6 for ch in channels):
            return
        time.sleep(0.5)
    sys.exit(f"La DAQ non ha applicato l'offset {offset} entro {timeout} s.")


def predicted(offset, sigma, nch):
    z = offset / (sigma * math.sqrt(2.0))
    return F_ADC * nch * 0.5 * math.erfc(z)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offsets", type=float, nargs="+",
                    default=[5, 4.5, 4, 3.5, 3, 2.5],
                    help="offset da provare, meglio in ordine decrescente")
    ap.add_argument("--seconds", type=float, default=20,
                    help="secondi di misura per punto (default 20)")
    ap.add_argument("--sigma", type=float, default=None,
                    help="rumore in Transparent Mode [conteggi], per la previsione. "
                         "Se omesso si usa quello nel file di stato")
    ap.add_argument("-d", "--data-dir", default=os.path.join(ROOT, "data"))
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "plots"))
    args = ap.parse_args()

    files = glob.glob(os.path.join(args.data_dir, "*.h5"))
    if not files:
        sys.exit(f"Nessuna run in corso: nessun file .h5 in {args.data_dir}")
    path = max(files, key=os.path.getmtime)

    st = status(args.data_dir)
    channels = [int(c["ch"]) for c in st["channels"]]
    sigma = args.sigma or 0.72

    print(f"file   : {os.path.basename(path)}")
    print(f"canali : {channels}      sigma assunto: {sigma} conteggi")
    print("\nATTENZIONE: il segnale deve essere spento. Se i PMT sono attivi,")
    print("            quello che misuri e' segnale piu' rumore.\n")
    print(f"  {'offset':>7} {'in sigma':>9} {'rate misurato':>15} {'atteso':>12}")

    rows = []
    for off in args.offsets:
        set_offset(args.data_dir, channels, off)
        time.sleep(1.0)                        # la soglia entra in vigore
        start = n_events(path)
        t0 = time.time()
        time.sleep(args.seconds)
        dt = time.time() - t0
        rate = (n_events(path) - start) / dt

        exp = predicted(off, sigma, len(channels))
        exp_s = f"{exp:10.1f} Hz" if exp < 1e4 else "    saturo"
        print(f"  {off:7g} {off/sigma:8.1f}σ {rate:12.1f} Hz {exp_s}")
        rows.append((off, rate, exp))

        if rate > 5000:
            print("     rate molto alto: mi fermo qui per non intasare la DAQ")
            break

    # --- grafico ---
    os.makedirs(args.out, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    x = np.array([r[0] for r in rows])
    y = np.array([max(r[1], 1e-3) for r in rows])
    ax.semilogy(x, y, "o-", label="misurato")

    xs = np.linspace(min(x) * 0.9, max(x) * 1.1, 100)
    ys = [max(predicted(v, sigma, len(channels)), 1e-3) for v in xs]
    ax.semilogy(xs, ys, "--", label=f"coda gaussiana, σ = {sigma}")

    ax.set_xlabel("offset [conteggi Transparent Mode]")
    ax.set_ylabel("rate di trigger [Hz]")
    ax.set_title("Pavimento di rumore del self-trigger", fontsize=11)
    ax.grid(alpha=.3, which="both")
    ax.legend()
    fig.tight_layout()
    out = os.path.join(args.out, "noise_scan.png")
    fig.savefig(out, dpi=120)
    plt.close(fig)

    print(f"\ngrafico: {out}")
    print("\nSe misura e previsione si sovrappongono, il modello di rumore regge e")
    print("le soglie espresse in sigma sono affidabili. Se la misura sta sopra,")
    print("c'e' una componente di rumore in piu' rispetto alla gaussiana.")


if __name__ == "__main__":
    main()
