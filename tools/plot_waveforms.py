#!/usr/bin/env python3
"""
Visualizzazione rapida delle forme d'onda acquisite dal DAQ V1742.

Legge i file HDF5 prodotti da DAQ-WC (sia .h5 che .h5.gz) e produce:
  - un riepilogo degli attributi di /config
  - le forme d'onda dei primi N eventi, un pannello per canale
  - la forma d'onda media per canale
  - la distribuzione delle ampiezze di picco

Pensato per girare via SSH senza display: di default salva dei PNG
invece di aprire finestre.

Esempi:
    python3 tools/plot_waveforms.py                      # ultimo file in data/
    python3 tools/plot_waveforms.py data/run.h5.gz -n 50
    python3 tools/plot_waveforms.py --live               # run ancora in corso
"""

import argparse
import glob
import os
import sys

import numpy as np

# Nessun display quando si lavora via SSH: va scelto prima di importare pyplot.
import matplotlib
if not os.environ.get("DISPLAY"):
    matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daqio import load, DaqFileError


# ----------------------------------------------------------------------
#  Individuazione del file
# ----------------------------------------------------------------------

def find_latest(data_dir):
    """Il file di dati piu' recente nella directory indicata."""
    files = glob.glob(os.path.join(data_dir, "*.h5")) + \
            glob.glob(os.path.join(data_dir, "*.h5.gz"))
    if not files:
        sys.exit(f"Nessun file .h5/.h5.gz in {data_dir}")
    return max(files, key=os.path.getmtime)


# ----------------------------------------------------------------------
#  Analisi
# ----------------------------------------------------------------------

def baseline_and_amplitude(data, pre_frac=0.15):
    """Baseline pre-impulso e ampiezza di picco, per evento e canale.

    La baseline e' la mediana della porzione iniziale della traccia (robusta
    rispetto a un eventuale impulso). L'ampiezza e' l'escursione massima
    rispetto alla baseline, con il segno del segnale.
    """
    n_pre = max(4, int(data.shape[2] * pre_frac))
    base = np.median(data[:, :, :n_pre], axis=2)
    corr = data - base[:, :, None]

    # Il verso del segnale si deduce dai dati, non si assume
    imax = np.max(corr, axis=2)
    imin = np.min(corr, axis=2)
    amp = np.where(np.abs(imin) > np.abs(imax), imin, imax)

    return base, corr, amp


def print_summary(hdr, data, base, amp, n_samp, tailcut):
    print("=" * 66)
    print("  CONFIGURAZIONE DELLA RUN")
    print("=" * 66)
    for k in sorted(hdr):
        print(f"  {k:<24} = {hdr[k]}")

    print()
    print(f"  formato file             = v{hdr.get('FormatVersion', 1)}")
    print(f"  eventi letti             = {data.shape[0]}")
    print(f"  campioni per canale      = {n_samp}"
          + (f"  (TailCut implicito = {tailcut})" if tailcut else ""))

    print()
    print("=" * 66)
    print("  STATISTICHE PER CANALE  (conteggi ADC)")
    print("=" * 66)
    print(f"  {'canale':<8} {'baseline':>10} {'rms':>8} {'ampiezza media':>16} {'max':>9}")
    for i, ch in enumerate(hdr["ChannelList"]):
        rms = float(np.std(data[:, i, :int(n_samp * 0.15)]))
        print(f"  ch{ch:<6} {np.mean(base[:, i]):>10.1f} {rms:>8.2f}"
              f" {np.mean(amp[:, i]):>16.1f} {np.max(np.abs(amp[:, i])):>9.1f}")
    print()


# ----------------------------------------------------------------------
#  Grafici
# ----------------------------------------------------------------------

def _grid(n):
    ncol = min(2, n)
    return (n + ncol - 1) // ncol, ncol


def plot_waveforms(corr, hdr, t_ns, n_show, out):
    channels = hdr["ChannelList"]
    nrow, ncol = _grid(len(channels))
    fig, axes = plt.subplots(nrow, ncol, figsize=(7 * ncol, 3.2 * nrow),
                             squeeze=False, sharex=True)

    n_show = min(n_show, corr.shape[0])
    for i, ch in enumerate(channels):
        ax = axes[i // ncol][i % ncol]
        for e in range(n_show):
            ax.plot(t_ns, corr[e, i], lw=0.6, alpha=0.5)
        ax.axhline(0, color="k", lw=0.8, ls=":")
        ax.set_title(f"ch{ch} — {n_show} eventi sovrapposti")
        ax.set_ylabel("ADC − baseline")
        ax.grid(alpha=0.25)
    for ax in axes[-1]:
        ax.set_xlabel("tempo [ns]")

    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


def plot_average(corr, hdr, t_ns, out):
    channels = hdr["ChannelList"]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    for i, ch in enumerate(channels):
        ax.plot(t_ns, corr[:, i].mean(axis=0), lw=1.4, label=f"ch{ch}")
    ax.axhline(0, color="k", lw=0.8, ls=":")
    ax.set_xlabel("tempo [ns]")
    ax.set_ylabel("ADC − baseline")
    ax.set_title(f"Forma d'onda media su {corr.shape[0]} eventi")
    ax.legend()
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


def plot_amplitudes(amp, hdr, out):
    channels = hdr["ChannelList"]
    nrow, ncol = _grid(len(channels))
    fig, axes = plt.subplots(nrow, ncol, figsize=(6 * ncol, 3.2 * nrow),
                             squeeze=False)
    for i, ch in enumerate(channels):
        ax = axes[i // ncol][i % ncol]
        ax.hist(amp[:, i], bins=min(60, max(10, amp.shape[0] // 4)))
        ax.set_xlabel("ampiezza di picco [ADC]")
        ax.set_ylabel("eventi")
        ax.set_title(f"ch{ch}")
        ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


# ----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?",
                    help="file .h5/.h5.gz (default: il piu' recente in data/)")
    ap.add_argument("-n", "--nevents", type=int, default=20,
                    help="eventi da sovrapporre nel grafico (default 20)")
    ap.add_argument("-m", "--max-events", type=int, default=None,
                    help="limita gli eventi letti (default: tutti)")
    ap.add_argument("-d", "--data-dir", default="data",
                    help="directory dei dati (default: data)")
    ap.add_argument("-o", "--outdir", default="plots",
                    help="directory dei PNG prodotti (default: plots)")
    ap.add_argument("--live", action="store_true",
                    help="legge via SWMR mentre la run e' in corso (richiede LiveMonitoring = true)")
    ap.add_argument("--show", action="store_true",
                    help="apre le finestre invece di salvare (richiede display)")
    args = ap.parse_args()

    path = args.file or find_latest(args.data_dir)
    print(f"File: {path}\n")

    try:
        hdr, data = load(path, max_events=args.max_events, live=args.live)
    except DaqFileError as exc:
        sys.exit(str(exc))

    n_samp = data.shape[2]
    tailcut = int(hdr.get("TailCut", 0))

    base, corr, amp = baseline_and_amplitude(data)
    print_summary(hdr, data, base, amp, n_samp, tailcut)

    dt_ns = float(hdr.get("SamplingTime", 1e-9)) * 1e9
    t_ns = np.arange(n_samp) * dt_ns

    if args.show:
        matplotlib.use(matplotlib.get_backend())

    os.makedirs(args.outdir, exist_ok=True)
    stem = os.path.basename(path).replace(".h5.gz", "").replace(".h5", "")
    produced = [
        plot_waveforms(corr, hdr, t_ns, args.nevents,
                       os.path.join(args.outdir, f"{stem}_waveforms.png")),
        plot_average(corr, hdr, t_ns,
                     os.path.join(args.outdir, f"{stem}_media.png")),
        plot_amplitudes(amp, hdr,
                        os.path.join(args.outdir, f"{stem}_ampiezze.png")),
    ]

    print("Grafici salvati:")
    for p in produced:
        print(f"  {p}")


if __name__ == "__main__":
    main()
