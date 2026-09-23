#!/usr/bin/env python3
"""
Calibrazione della soglia di self-trigger: offset -> mV.

Cambia l'offset a run in corso, raccoglie per un tempo fissato a ogni punto,
e ricava la soglia efficace di ciascun offset dalla CURVA DI EFFICIENZA:

    eff_i(A) = spettro(offset_i, A) / spettro(offset_riferimento, A)

Lo spettro fisico si semplifica nel rapporto, quindi non serve conoscerlo. Il
punto al 50% e' stabile perche' si appoggia a migliaia di eventi per bin, a
differenza del bordo inferiore della distribuzione, che dipende da quanti
impulsi piccoli capita di avere nel campione e varia di un fattore 2 fra run.

Ogni curva viene normalizzata al proprio plateau ad alta ampiezza, dove
l'efficienza e' 1 per costruzione: cosi' si correggono da sole le differenze
di tempo vivo e di dead time fra i punti.

Serve una run gia' avviata con SelfTrigger = true.

    python3 tools/threshold_scan.py --offsets 4 6 8 12 16 --seconds 90
"""

import argparse
import glob
import json
import os
import sys
import time

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daqio import load, baseline_amplitude, count_events, DaqFileError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ----------------------------------------------------------------------
#  Dialogo con la DAQ
# ----------------------------------------------------------------------

def status(data_dir):
    with open(os.path.join(data_dir, "live-status.json")) as f:
        return json.load(f)


def current_file(data_dir):
    files = glob.glob(os.path.join(data_dir, "*.h5"))
    if not files:
        sys.exit(f"Nessuna run in corso: nessun file .h5 in {data_dir}")
    return max(files, key=os.path.getmtime)


def n_events(path):
    return count_events(path, live=True)


def set_offset(data_dir, channels, offset, timeout=15):
    """Scrive il nuovo offset e attende che la DAQ lo confermi."""
    before = status(data_dir).get("generation")
    with open(os.path.join(data_dir, "live-threshold.txt"), "w") as f:
        for ch in channels:
            f.write(f"{ch} {offset}\n")

    t0 = time.time()
    while time.time() - t0 < timeout:
        st = status(data_dir)
        applied = {int(c["ch"]): c["offset"] for c in st["channels"]}
        if all(abs(applied.get(ch, -1) - offset) < 1e-6 for ch in channels):
            return st
        time.sleep(0.5)
    sys.exit(f"La DAQ non ha applicato l'offset {offset} entro {timeout} s.")


# ----------------------------------------------------------------------
#  Analisi
# ----------------------------------------------------------------------

def efficiency_point(ref_hist, hist, centres, amin, min_counts=200):
    """Ampiezza al 50%, plateau usato, curva normalizzata.

    Tre accorgimenti, ognuno per un errore visto sui dati veri:

    - si ignorano le ampiezze sotto `amin`, dove vive la popolazione di eventi
      SENZA impulso (in modo "paired" il trigger di un canale fa acquisire
      anche l'altro). Il loro peso cambia fra i punti dello scan e produceva
      una gobba spuria che veniva scambiata per l'attraversamento del 50%;
    - il plateau si misura dove la statistica c'e' davvero, cioe' nei bin in
      cui il riferimento ha almeno `min_counts` eventi, invece che in una
      regione fissa ad alta ampiezza dove i conteggi sono zero o due e il
      rapporto vale 0, 1 o 2;
    - l'efficienza NON satura a 1 in assoluto: con l'ADC a 30 MHz un impulso
      da ~10 ns viene campionato sopra soglia solo in una frazione dei casi,
      quindi il plateau e' fisico e dipende dall'offset. Normalizzarvi resta
      giusto, purche' lo si misuri bene.
    """
    usable = (centres >= amin) & (ref_hist >= min_counts)
    if usable.sum() < 4:
        return None, None, np.full_like(centres, np.nan, dtype=float)

    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(ref_hist > 0, hist / ref_hist, np.nan)

    # plateau: meta' alta della regione con statistica sufficiente
    idx = np.where(usable)[0]
    hi  = idx[len(idx) // 2:]
    plateau = np.nanmedian(ratio[hi])
    if not np.isfinite(plateau) or plateau <= 0:
        return None, None, ratio

    eff = np.where(usable, ratio / plateau, np.nan)

    ok = np.isfinite(eff)
    x, y = centres[ok], eff[ok]
    for k in range(len(x) - 1):
        if y[k] < 0.5 <= y[k + 1]:
            frac = (0.5 - y[k]) / (y[k + 1] - y[k])
            return x[k] + frac * (x[k + 1] - x[k]), plateau, eff
    return None, plateau, eff


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offsets", type=float, nargs="+", required=True,
                    help="offset da provare; il primo e' il riferimento e deve "
                         "essere il piu' basso che non triggeri sul rumore")
    ap.add_argument("--events", type=int, default=12000,
                    help="eventi da raccogliere per ogni punto (default 12000). "
                         "A statistica fissa i punti pesano uguale: a tempo fisso "
                         "gli offset alti, che hanno rate molto piu' basso, "
                         "restavano troppo poveri")
    ap.add_argument("--max-seconds", type=float, default=300,
                    help="tempo massimo per punto, se il rate e' troppo basso")
    ap.add_argument("-d", "--data-dir", default=os.path.join(ROOT, "data"))
    ap.add_argument("--vpp", type=float, default=1.0,
                    help="range di ingresso del digitizer in Vpp (default 1.0)")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "plots"))
    args = ap.parse_args()

    mv = 1000.0 * args.vpp / 4096.0
    path = current_file(args.data_dir)
    st = status(args.data_dir)
    channels = [int(c["ch"]) for c in st["channels"]]

    print(f"file      : {os.path.basename(path)}")
    print(f"canali    : {channels}")
    print(f"offset    : {args.offsets}")
    print(f"per punto : {args.events} eventi, al massimo {args.max_seconds:g} s\n")

    # --- raccolta -----------------------------------------------------
    segments = []
    for off in args.offsets:
        set_offset(args.data_dir, channels, off)
        start = n_events(path)
        t0 = time.time()
        while True:
            time.sleep(2)
            end = n_events(path)
            if end - start >= args.events or time.time() - t0 > args.max_seconds:
                break
        dt = time.time() - t0
        rate = (end - start) / dt
        flag = "" if end - start >= args.events else "   POCHI EVENTI: curva rumorosa"
        print(f"  offset {off:<6g} eventi {end-start:<8d} rate {rate:7.1f} Hz "
              f"in {dt:5.0f} s{flag}")
        segments.append((off, start, end))

    # Salvati per poter rianalizzare senza rifare la presa dati
    seg_file = os.path.join(args.out, "threshold_scan_segments.json")
    os.makedirs(args.out, exist_ok=True)
    with open(seg_file, "w") as f:
        json.dump({"file": os.path.basename(path), "vpp": args.vpp,
                   "segments": segments}, f, indent=2)
    print(f"\nsegmenti salvati in {seg_file}")

    # --- analisi ------------------------------------------------------
    print("\nanalisi…")
    first = min(s for _, s, _ in segments)
    hdr, data = load(path, last=n_events(path) - first, live=True)
    base0 = int(hdr["NEventsInFile"]) - data.shape[0]
    _, _, amp, noise = baseline_amplitude(data)

    edges   = np.linspace(0, 400, 81)          # conteggi Output Mode
    centres = 0.5 * (edges[1:] + edges[:-1])
    results = {ch: [] for ch in channels}

    fig, axes = plt.subplots(1, len(channels), figsize=(6 * len(channels), 4.2),
                             squeeze=False)

    for i, ch in enumerate(hdr["ChannelList"]):
        ch = int(ch)
        hists = {}
        for off, s, e in segments:
            sl = slice(max(0, s - base0), max(0, e - base0))
            hists[off], _ = np.histogram(np.abs(amp[sl, i]), bins=edges)

        ref = hists[segments[0][0]]
        ax = axes[0][i]
        for off, _, _ in segments[1:]:
            # sotto 8 volte il rumore c'e' la popolazione senza impulso
            amin = 8 * float(np.median(noise[:, i]))
            x50, plateau, eff = efficiency_point(ref, hists[off], centres, amin)
            ax.plot(centres, eff, marker=".", lw=1, label=f"offset {off:g}")
            if x50:
                results[ch].append((off, x50))
                print(f"  ch{ch} offset {off:<6g} 50% a {x50:6.1f} conteggi "
                      f"= {x50*mv:5.2f} mV   (plateau {plateau:.2f})")
            else:
                print(f"  ch{ch} offset {off:<6g} punto al 50% non determinato")

        ax.axhline(0.5, color="k", lw=.8, ls=":")
        ax.set_xlabel("ampiezza [conteggi ADC]")
        ax.set_ylabel(f"efficienza relativa a offset {segments[0][0]:g}")
        ax.set_title(f"ch{ch}", fontsize=10)
        ax.set_ylim(0, 1.3)
        ax.grid(alpha=.25)
        ax.legend(fontsize=8)

    os.makedirs(args.out, exist_ok=True)
    fig.tight_layout()
    fig.savefig(os.path.join(args.out, "threshold_scan_efficiency.png"), dpi=120)
    plt.close(fig)

    # --- retta di calibrazione ---------------------------------------
    print("\ncalibrazione:")
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    for ch in channels:
        pts = results[ch]
        if len(pts) < 2:
            print(f"  ch{ch}: punti insufficienti")
            continue
        x = np.array([p[0] for p in pts])
        y = np.array([p[1] for p in pts])
        slope, icept = np.polyfit(x, y, 1)
        resid = y - (slope * x + icept)
        print(f"  ch{ch}: soglia[conteggi] = {slope:.2f} * offset + {icept:.1f}")
        print(f"         {slope*mv:.3f} mV per unita' di offset, "
              f"scarto max dalla retta {np.abs(resid).max():.1f} conteggi")
        ax.plot(x, y * mv, "o", label=f"ch{ch}")
        xs = np.linspace(0, x.max() * 1.1, 50)
        ax.plot(xs, (slope * xs + icept) * mv, "-", lw=1, alpha=.7)

    ax.set_xlabel("offset [conteggi Transparent Mode]")
    ax.set_ylabel("soglia al 50% [mV]")
    ax.grid(alpha=.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(args.out, "threshold_scan_calibration.png"), dpi=120)
    plt.close(fig)

    print(f"\ngrafici in {args.out}/")
    print("  threshold_scan_efficiency.png    curve di efficienza")
    print("  threshold_scan_calibration.png   retta offset -> mV")


if __name__ == "__main__":
    main()
