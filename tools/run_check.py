#!/usr/bin/env python3
"""Riepilogo di una run: ampiezza, larghezza, purezza, rumore, deriva, rate.

Sono le stesse grandezze che servono a ogni run di riferimento prima di uno
scan in soglia. Funziona sia a run in corso sia su file chiusi (.h5.gz).

    python3 tools/run_check.py                 # ultimo file in data/
    python3 tools/run_check.py --seconds 0     # senza misurare il rate
    python3 tools/run_check.py data/PMT_selftrig_0101_2.5Gs_10PT.h5
"""

import argparse
import glob
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daqio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SOGLIA_IMPULSO = -40       # conteggi sotto i quali l'evento ha un impulso vero
FINESTRA_50NS = (40, 60)   # ns: dove il self-trigger mette l'impulso
CONTEGGIO_MV = 1000.0 / 4096
ATT_CONTINUA = 1.89        # attenuazione del Transparent Mode in continua


def ultimo_file(data_dir):
    f = sorted(glob.glob(os.path.join(data_dir, "*.h5")) +
               glob.glob(os.path.join(data_dir, "*.h5.gz")),
               key=os.path.getmtime)
    if not f:
        sys.exit("Nessun file di dati in " + data_dir)
    return f[-1]


def stato(data_dir):
    try:
        with open(os.path.join(data_dir, "live-status.json")) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def larghezza(traccia, dt_ns):
    """FWHM contando solo i campioni CONTIGUI sotto meta' altezza.

    Senza il vincolo di contiguita' il conteggio include campioni di rumore
    sparsi altrove nella traccia, e la larghezza esce sistematicamente doppia.
    """
    k = int(np.argmin(traccia))
    meta = 0.5 * traccia[k]
    lo = hi = k
    while lo > 0 and traccia[lo - 1] < meta:
        lo -= 1
    while hi < len(traccia) - 1 and traccia[hi + 1] < meta:
        hi += 1
    return (hi - lo + 1) * dt_ns


def analizza(path, live, max_eventi):
    f, tmp = daqio.open_file(path, live=live)
    try:
        hdr = daqio.read_header(f)
        w = f["events/waveforms"]
        n_tot, ns = w.shape[0], hdr["SamplesPerChannel"]
        chans = list(hdr["ChannelList"])
        nch = len(chans)
        dt = hdr["SamplingTime"] * 1e9
        a0 = max(0, n_tot - max_eventi) if max_eventi else 0
        n = n_tot - a0
        amp = np.empty((n, nch)); pos = np.empty((n, nch))
        dc = np.empty((n, nch)); rms = np.empty((n, nch))
        for a in range(a0, n_tot, 2000):
            z = min(a + 2000, n_tot)
            d = np.asarray(w[a:z, :]).reshape(z - a, nch, ns).astype(np.float32)
            m = np.median(d, axis=2)
            s = d - m[:, :, None]
            i, j = a - a0, z - a0
            dc[i:j] = m
            amp[i:j] = s.min(axis=2)
            pos[i:j] = np.argmin(s, axis=2) * dt
            rms[i:j] = 1.4826 * np.median(np.abs(s), axis=2)
    finally:
        f.close()
        daqio._cleanup(tmp)
    return hdr, chans, dt, n_tot, amp, pos, dc, rms


def deriva(x, n=101):
    """Media mobile con clipping: isola la deriva dalla dispersione.

    Su run corte la finestra va accorciata, altrimenti copre tutti i dati e
    restituisce una costante: la deriva risulterebbe zero per costruzione.
    """
    n = min(n, max(5, len(x) // 6))
    if n % 2 == 0:
        n += 1
    c = np.median(x)
    x = np.clip(x, c - 8, c + 8)
    pad = np.pad(x, n // 2, mode="edge")
    return np.convolve(pad, np.ones(n) / n, mode="valid")[:len(x)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?")
    ap.add_argument("-d", "--data-dir", default=os.path.join(ROOT, "data"))
    ap.add_argument("-s", "--seconds", type=float, default=20,
                    help="durata della misura del rate (0 = non misurarlo)")
    ap.add_argument("-m", "--max-events", type=int, default=4000,
                    help="quanti eventi finali analizzare")
    args = ap.parse_args()

    path = args.file or ultimo_file(args.data_dir)
    live = not path.endswith(".gz")
    print("file:", os.path.basename(path))

    st = stato(args.data_dir)
    if st and st.get("file") and os.path.basename(path).rstrip(".gz") == st["file"]:
        for c in st["channels"]:
            print("  soglia ch%d: offset %g  ->  %d   (piedistallo %.2f, distanza %.2f)"
                  % (c["ch"], c["offset"], c["threshold"], c["baseline"],
                     c["baseline"] - c["threshold"]))

    rate = None
    if args.seconds > 0 and live:
        try:
            n0 = daqio.count_events(path, live=True); t0 = time.time()
            time.sleep(args.seconds)
            n1 = daqio.count_events(path, live=True); t1 = time.time()
            rate = (n1 - n0) / (t1 - t0)
            print("rate: %.1f Hz   (%d eventi in %.1f s)" % (rate, n1 - n0, t1 - t0))
        except daqio.DaqFileError as e:
            print("rate: non misurabile (", e, ")")

    hdr, chans, dt, n_tot, amp, pos, dc, rms = analizza(path, live, args.max_events)
    print("eventi: %d totali, %d analizzati" % (n_tot, len(amp)))
    if len(amp) < 600:
        print("  ATTENZIONE: pochi eventi, la deriva non e' ancora misurabile")
    print()

    for i, ch in enumerate(chans):
        buoni = (amp[:, i] < SOGLIA_IMPULSO) & (pos[:, i] > FINESTRA_50NS[0]) \
                                             & (pos[:, i] < FINESTRA_50NS[1])
        pur = 100.0 * buoni.mean()
        print("ch%d:  rumore %.2f cnt   impulso a ~50 ns nel %.1f%% degli eventi"
              % (ch, np.median(rms[:, i]), pur))
        if buoni.sum() > 20:
            a = amp[buoni, i]
            q = np.percentile(a, [5, 25, 50, 75, 95])
            print("      ampiezza  %.1f cnt = %.2f mV   quartili %.0f/%.0f   5-95%%: %.0f/%.0f"
                  % (q[2], q[2] * CONTEGGIO_MV, q[1], q[3], q[0], q[4]))
        dd = deriva(dc[:, i])
        print("      piedistallo %.2f   deriva %.2f cnt (Output) = %.2f cnt (Transparent)"
              % (dc[:, i].mean(), dd.max() - dd.min(),
                 (dd.max() - dd.min()) / ATT_CONTINUA))

    # la larghezza si misura solo sul canale che ha davvero il segnale
    i = int(np.argmax([( (amp[:, k] < SOGLIA_IMPULSO) &
                         (pos[:, k] > FINESTRA_50NS[0]) &
                         (pos[:, k] < FINESTRA_50NS[1]) ).sum()
                       for k in range(len(chans))]))
    f, tmp = daqio.open_file(path, live=live)
    try:
        w = f["events/waveforms"]
        ns = hdr["SamplesPerChannel"]; nch = len(chans)
        sel = np.where((amp[:, i] < SOGLIA_IMPULSO) &
                       (pos[:, i] > FINESTRA_50NS[0]) &
                       (pos[:, i] < FINESTRA_50NS[1]))[0]
        if len(sel):
            a0 = w.shape[0] - len(amp)
            larg = []
            for k in sel[:1500]:
                tr = np.asarray(w[a0 + k, :]).reshape(nch, ns).astype(np.float32)[i]
                larg.append(larghezza(tr - np.median(tr), dt))
            larg = np.array(larg)
            print()
            print("ch%d:  larghezza %.2f ns   (10-90%%: %.2f-%.2f)"
                  % (chans[i], np.median(larg), np.percentile(larg, 10),
                     np.percentile(larg, 90)))
    finally:
        f.close()
        daqio._cleanup(tmp)

    if len(chans) >= 2:
        d0 = deriva(dc[:, 0]) - deriva(dc[:, 0])[0]
        d1 = deriva(dc[:, 1]) - deriva(dc[:, 1])[0]
        com = np.ptp(d0 + d1) / 2
        dif = np.ptp(d0 - d1)
        print()
        print("deriva: %.2f cnt in comune fra i canali, %.2f cnt differenziale"
              % (com, dif))
        print("        (comune = elettronica; differenziale = sorgente su un canale)")

    if rate is not None and st:
        print()
        print("nota: con generatore a rate noto R, efficienza = %.1f / R" % rate)


if __name__ == "__main__":
    main()
