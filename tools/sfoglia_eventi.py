#!/usr/bin/env python3
"""Sfoglia gli eventi di un run, uno per pagina: un grafico per canale.

    python3 tools/sfoglia_eventi.py FILE [EVENTO]
    python3 tools/sfoglia_eventi.py FILE EVENTO --png uscita.png   (solo l'immagine)

Nel terminale: Invio = evento successivo, un numero = vai a quell'evento,
b = indietro, q = esci. Nella finestra: Invio o freccia destra avanti, freccia
sinistra indietro.

Legge un evento per volta, quindi va bene anche per i run da qualche GB, e
apre i .h5.gz e i file di un run ancora in corso. I segnali sono disegnati con
la loro polarita' vera, senza invertirli.
"""

# --------------------------------------------------------------------------
#  CONFIGURAZIONE
# --------------------------------------------------------------------------
RIGHE, COLONNE = 2, 8          # 16 grafici, un canale ciascuno
UNITA = "counts"               # "counts" (conteggi ADC) oppure "mV"
TOGLI_PIEDISTALLO = False      # True: ogni traccia meno la mediana dei primi PIEDISTALLO_NS
PIEDISTALLO_NS = 200.0
STESSA_SCALA_Y = False         # True: lo stesso intervallo verticale su tutti i canali
DIMENSIONE = (19, 6.5)         # pollici
SPESSORE = 0.7
COLORE = "#1f4e9c"
# --------------------------------------------------------------------------

import argparse
import os
import select
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402

import daqio  # noqa: E402


def apri(percorso):
    """(file h5py, temporaneo, dal vivo). Un run in corso si apre in SWMR."""
    try:
        f, tmp = daqio.open_file(percorso, live=False)
        return f, tmp, False
    except daqio.DaqFileError:
        f, tmp = daqio.open_file(percorso, live=True)
        return f, tmp, True


def passo_ns(hdr):
    # SamplingTime e' in secondi (V1742; VX2730B dal 2026-11-11). I primi file
    # del VX2730B lo avevano in ns (2.0): un valore cosi' grande lo e'.
    st = float(hdr.get("SamplingTime", 1e-9))
    return st if st > 1e-6 else st * 1e9


def mv_per_conteggio(hdr, ncan):
    """mV per conteggio per canale: ADCToVolts del file (VX2730B, per canale e
    col guadagno), altrimenti 1 V su 12 bit del V1742."""
    a = hdr.get("ADCToVolts")
    if a is not None and len(np.atleast_1d(a)) == ncan:
        return np.atleast_1d(a).astype(float) * 1e3
    return np.full(ncan, 1000.0 / 4096.0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("evento", nargs="?", type=int, default=0)
    ap.add_argument("--png", help="disegna solo l'evento dato in questo file ed esce")
    a = ap.parse_args()

    import matplotlib
    if a.png:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    f, tmp, vivo = apri(a.file)
    try:
        hdr = daqio.read_header(f)
        ds = f["/events/waveforms"]
        canali = [int(c) for c in np.atleast_1d(hdr["ChannelList"])]
        ns = int(hdr["SamplesPerChannel"])
        ncan = len(canali)
        dt = passo_ns(hdr)
        t = np.arange(ns) * dt
        conv = mv_per_conteggio(hdr, ncan) if UNITA == "mV" else np.ones(ncan)
        n_ped = max(1, min(ns, int(PIEDISTALLO_NS / dt)))
        contatore = f["/events/event_counter"] if "/events/event_counter" in f else None
        tempo = f["/events/timestamp"] if "/events/timestamp" in f else None
        unita_t = float(tempo.attrs.get("UnitNs", 8.0)) if tempo is not None else None
        nome = os.path.basename(a.file)
        unita_y = "mV" if UNITA == "mV" else "ADC counts"
        if TOGLI_PIEDISTALLO:
            unita_y += " - pedestal"

        fig, assi = plt.subplots(RIGHE, COLONNE, figsize=DIMENSIONE, sharex=True)
        assi = np.atleast_1d(assi).ravel()
        linee = []
        for k, ax in enumerate(assi):
            if k >= ncan:
                ax.set_visible(False)
                continue
            (l,) = ax.plot(t, np.zeros(ns), lw=SPESSORE, color=COLORE)
            linee.append(l)
            ax.set_title("ch%d" % canali[k], fontsize=9, loc="left")
            ax.grid(alpha=0.3)
            ax.tick_params(labelsize=7)
            if k % COLONNE == 0:
                ax.set_ylabel(unita_y, fontsize=8)
            if k >= (RIGHE - 1) * COLONNE or k + COLONNE >= ncan:
                ax.set_xlabel("time [ns]", fontsize=8)
        if ncan > RIGHE * COLONNE:
            print("Attenzione: %d canali, se ne vedono i primi %d (RIGHE x COLONNE)."
                  % (ncan, RIGHE * COLONNE))
        fig.tight_layout(rect=(0, 0, 1, 0.95))

        def n_eventi():
            if vivo:
                ds.refresh()
            return ds.shape[0]

        def disegna(i):
            w = ds[i].reshape(ncan, ns).astype(float)
            if TOGLI_PIEDISTALLO:
                w -= np.median(w[:, :n_ped], axis=1, keepdims=True)
            w *= conv[:, None]
            for k, l in enumerate(linee):
                l.set_ydata(w[k])
                ax = assi[k]
                ax.relim()
                ax.autoscale_view()
            if STESSA_SCALA_Y:
                lo, hi = w[:len(linee)].min(), w[:len(linee)].max()
                pad = 0.05 * (hi - lo or 1)
                for ax in assi[:len(linee)]:
                    ax.set_ylim(lo - pad, hi + pad)
            titolo = "%s  -  event %d of %d" % (nome, i, n_eventi())
            if contatore is not None:
                if vivo:
                    contatore.refresh()
                titolo += "   trigger id %d" % int(contatore[i])
            if tempo is not None:
                if vivo:
                    tempo.refresh()
                titolo += "   t = %.6f s" % (int(tempo[i]) * unita_t * 1e-9)
            fig.suptitle(titolo, fontsize=10)
            fig.canvas.draw_idle()

        n = n_eventi()
        if n == 0:
            sys.exit("Nessun evento nel file.")
        i = min(max(0, a.evento), n - 1)

        if a.png:
            disegna(i)
            fig.savefig(a.png, dpi=100)
            print("scritto", a.png)
            return

        # Comandi dalla finestra: si appoggiano qui e il ciclo li legge.
        dalla_finestra = []
        def tasto(ev):
            if ev.key in ("enter", "right", "pagedown"):
                dalla_finestra.append("")
            elif ev.key in ("left", "backspace", "pageup"):
                dalla_finestra.append("b")
        fig.canvas.mpl_connect("key_press_event", tasto)
        plt.ion()
        fig.show()

        def comando(i):
            # Si aspetta il comando lasciando lavorare la finestra (zoom,
            # spostamento): con un input() secco resterebbe congelata.
            print("[event %d/%d] Enter = next, number = go to, b = back, q = quit: "
                  % (i, n_eventi() - 1), end="", flush=True)
            while True:
                if not plt.fignum_exists(fig.number):
                    return "q"
                if dalla_finestra:
                    print()
                    return dalla_finestra.pop(0)
                pronto, _, _ = select.select([sys.stdin], [], [], 0)
                if pronto:
                    riga = sys.stdin.readline()
                    return "q" if riga == "" else riga.strip().lower()
                plt.pause(0.05)

        while True:
            disegna(i)
            r = comando(i)
            if r == "q":
                break
            if r == "b":
                i = max(0, i - 1)
            elif r.isdigit():
                i = min(int(r), n_eventi() - 1)
            elif i + 1 < n_eventi():
                i += 1
            else:
                print("  last event of the file%s." % (": waiting for more, the run is going on" if vivo else ""))
        plt.close("all")
    finally:
        f.close()
        daqio._cleanup(tmp)


if __name__ == "__main__":
    main()
