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
from daqio import count_events

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F_ADC = 30e6          # campionamento dell'ADC in Transparent Mode, ~30 MHz


def status(data_dir):
    with open(os.path.join(data_dir, "live-status.json")) as f:
        return json.load(f)


def n_events(path):
    return count_events(path, live=True)


def set_offset(data_dir, channels, offset, timeout=15):
    """Imposta l'offset e restituisce cio' che la DAQ ha davvero scritto.

    L'offset richiesto non e' la soglia: il registro e' a 12 bit, quindi
    offset diversi possono finire sullo stesso valore intero. Serve la
    distanza vera fra baseline e soglia, che e' la grandezza fisica.
    """
    with open(os.path.join(data_dir, "live-threshold.txt"), "w") as f:
        for ch in channels:
            f.write(f"{ch} {offset}\n")
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = status(data_dir)
        applied = {int(c["ch"]): c["offset"] for c in st["channels"]}
        if all(abs(applied.get(ch, -1) - offset) < 1e-6 for ch in channels):
            return {int(c["ch"]): (c["baseline"], int(c["threshold"]))
                    for c in st["channels"]}
        time.sleep(0.5)
    sys.exit(f"La DAQ non ha applicato l'offset {offset} entro {timeout} s.")


def predicted(distances, sigma):
    """Rate atteso sommando i canali, ognuno con la sua distanza vera."""
    return sum(F_ADC * 0.5 * math.erfc(d / (sigma * math.sqrt(2.0)))
               for d in distances)


def sigma_from_rate(distances, rate):
    """Sigma che riprodurrebbe il rate misurato, per bisezione."""
    if rate <= 0:
        return None
    lo, hi = 0.05, 5.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if predicted(distances, mid) < rate:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offsets", type=float, nargs="+",
                    default=[6, 5, 4, 3, 2],
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

    # Senza baseline registrata due scan non sono confrontabili: la "distanza"
    # e' baseline meno soglia, e la baseline si misura una volta sola all'avvio
    # della run. Se si sposta fra una run e l'altra -- per esempio perche' e'
    # cambiata la corrente di anodo -- distanze nominalmente uguali non lo sono.
    print(f"file   : {os.path.basename(path)}")
    print(f"canali : {channels}      sigma assunto: {sigma} conteggi")
    print("baseline all'avvio della run (da live-status.json):")
    for c in st["channels"]:
        print(f"   ch{int(c['ch'])}: {c['baseline']:.3f}")
    print("\nATTENZIONE: il segnale deve essere spento. Se i PMT sono attivi,")
    print("            quello che misuri e' segnale piu' rumore.\n")
    print(f"  {'offset':>7} {'soglie':>13} {'distanza':>10} {'rate misurato':>16} "
          f"{'atteso':>11} {'sigma implicito':>16}")

    rows = []
    prev_thr = None
    for off in args.offsets:
        applied = set_offset(args.data_dir, channels, off)
        thr = tuple(applied[ch][1] for ch in channels)
        dists = [applied[ch][0] - applied[ch][1] for ch in channels]

        if thr == prev_thr:
            print(f"  {off:7g} {str(thr):>13}   COLLASSA sulla soglia precedente, salto")
            continue
        prev_thr = thr

        time.sleep(1.0)                        # la soglia entra in vigore
        # Gli estremi vanno registrati, non ricostruiti dopo: dedurli da
        # rate x durata sbaglia appena la DAQ perde eventi o il rate varia
        # dentro il passo, e l'analisi finisce per campionare il segmento
        # sbagliato senza accorgersene.
        start = n_events(path)
        t0 = time.time()
        time.sleep(args.seconds)
        dt = time.time() - t0
        counts = n_events(path) - start
        rate = counts / dt

        # Zero conteggi non e' "rate nullo": e' un limite superiore
        meas = (f"{rate:10.1f} Hz" if counts > 0
                else f"  < {3.0/dt:5.2f} Hz")
        exp = predicted(dists, sigma)
        exp_s = f"{exp:8.1f} Hz" if exp < 1e4 else "  saturo"
        sfit = sigma_from_rate(dists, rate)
        sfit_s = f"{sfit:14.2f}" if sfit else "             -"

        print(f"  {off:7g} {str(thr):>13} {np.mean(dists):9.2f} {meas:>16} "
              f"{exp_s:>11} {sfit_s}")
        rows.append((off, np.mean(dists), rate, exp, counts, start, start + counts))

        if rate > 5000:
            print("     rate molto alto: mi fermo qui per non intasare la DAQ")
            break

    # --- grafico ---
    os.makedirs(args.out, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    x = np.array([r[1] for r in rows])                 # distanza vera
    y = np.array([max(r[2], 1e-3) for r in rows])
    det = np.array([r[4] > 0 for r in rows])
    ax.semilogy(x[det], y[det], "o-", label="misurato")
    if (~det).any():
        ax.semilogy(x[~det], y[~det], "v", color="grey", label="limite superiore")

    xs = np.linspace(min(x) * 0.9, max(x) * 1.1, 100)
    ys = [max(predicted([v] * len(channels), sigma), 1e-3) for v in xs]
    ax.semilogy(xs, ys, "--", label=f"coda gaussiana, σ = {sigma}")

    fitted = [sigma_from_rate([r[1]] * len(channels), r[2]) for r in rows if r[4] > 0]
    if fitted:
        sm = float(np.median(fitted))
        ax.semilogy(xs, [max(predicted([v] * len(channels), sm), 1e-3) for v in xs],
                    ":", label=f"σ implicito dai dati = {sm:.2f}")

    ax.set_xlabel("distanza baseline − soglia [conteggi]")
    ax.set_ylabel("rate di trigger [Hz]")
    ax.set_title("Pavimento di rumore del self-trigger", fontsize=11)
    ax.grid(alpha=.3, which="both")
    ax.legend()
    fig.tight_layout()
    out = os.path.join(args.out, "noise_scan.png")
    fig.savefig(out, dpi=120)
    plt.close(fig)

    # Misura salvata per intero, cosi' due scan si possono confrontare davvero
    rec = {
        "file": os.path.basename(path),
        "quando": time.strftime("%Y-%m-%d %H:%M:%S"),
        "canali": channels,
        "baseline": {str(int(c["ch"])): c["baseline"] for c in st["channels"]},
        "sigma_assunto": sigma,
        "secondi_per_punto": args.seconds,
        "punti": [{"offset": r[0], "distanza": r[1], "rate": r[2],
                   "atteso": r[3], "conteggi": r[4],
                   "eventi_da": r[5], "eventi_a": r[6]} for r in rows],
    }
    stamp = time.strftime("%Y%m%d_%H%M%S")
    rec_path = os.path.join(args.out, f"noise_scan_{stamp}.json")
    with open(rec_path, "w") as f:
        json.dump(rec, f, indent=2)

    print(f"\ngrafico: {out}")
    print(f"misura : {rec_path}")
    if fitted:
        print(f"\nsigma implicito dai punti misurati: {np.median(fitted):.2f} conteggi "
              f"(assunto {sigma})")
        print("Se i valori nella colonna 'sigma implicito' concordano fra loro, il")
        print("modello gaussiano regge e quel numero e' il rumore vero del")
        print("comparatore. Se divergono, la coda non e' gaussiana.")


if __name__ == "__main__":
    main()
