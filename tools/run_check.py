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
import efficienza

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SOGLIA_MIN     = 15        # conteggi: il piu' permissivo che abbia senso
SIGMA_IMPULSO  = 8         # oppure questo numero di sigma di rumore, se maggiore


def soglia_impulso(rms):
    """Ampiezza sotto la quale si considera che l'evento contenga un impulso.

    Scritta fissa a -40 conteggi tagliava fuori segnali perfettamente validi:
    lo stesso impulso misurato -45 a 2.5 GS/s vale -37 a 1 GS/s, perche' con un
    campione ogni nanosecondo il picco cade fra due punti. Il taglio si ricava
    invece dal rumore del canale, restando comunque ben sopra il massimo che il
    rumore puo' produrre su una traccia intera (~4-5 sigma).
    """
    # Positiva: le ampiezze qui sono misurate DALLA PARTE DELL'IMPULSO di
    # ogni canale, quindi positive per PMT e SiPM allo stesso modo.
    return max(SOGLIA_MIN, SIGMA_IMPULSO * float(rms))
TOLLERANZA_NS  = 15        # semiampiezza della finestra in cui cercare l'impulso
LATENZA_NS     = {"paired": 320.0, "global": 420.0}


def finestra_attesa(hdr):
    """Dove il self-trigger mette l'impulso, (da, a) in ns.

    Non e' una costante: dipende da frequenza di campionamento e
    PostTriggerSize. A 2.5 GS/s con PostTriggerSize 10% cade a ~50 ns, a
    1 GS/s a ~600 ns. Scriverla fissa a 40-60 ns fa dire allo strumento
    "nessun impulso" su una run perfettamente sana presa a un'altra frequenza.

        posizione = (1 - PostTriggerSize) * finestra - latenza
    """
    try:
        dt = float(hdr["SamplingTime"]) * 1e9
        n = int(hdr["SamplesPerChannel"])
        post = float(hdr["PostTriggerSize"]) / 100.0
        modo = str(hdr.get("SelfTriggerMode", "paired"))
    except (KeyError, TypeError, ValueError):
        return None, None
    lat = LATENZA_NS.get(modo, LATENZA_NS["paired"])
    pos = (1.0 - post) * (n * dt) - lat
    return pos, (pos - TOLLERANZA_NS, pos + TOLLERANZA_NS)
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
    """FWHM contando solo i campioni CONTIGUI sopra meta' altezza.

    La traccia va data gia' ORIENTATA (impulso verso l'alto) e senza
    piedistallo. Senza il vincolo di contiguita' il conteggio include campioni
    di rumore sparsi altrove nella traccia, e la larghezza esce
    sistematicamente doppia.
    """
    k = int(np.argmax(traccia))
    meta = 0.5 * traccia[k]
    lo = hi = k
    while lo > 0 and traccia[lo - 1] > meta:
        lo -= 1
    while hi < len(traccia) - 1 and traccia[hi + 1] > meta:
        hi += 1
    return (hi - lo + 1) * dt_ns


def versi(path, chans, d):
    """Verso dell'impulso per canale: dal DCOffset nel log della run, se no
    dai dati. Stessa regola di monitor ed efficienza."""
    base = path[:-3] if path.endswith(".gz") else path
    segno = efficienza._segni_per([int(c) for c in chans],
                                  efficienza.polarita_dal_log(os.path.splitext(base)[0] + ".log"))
    if segno is not None:
        return segno, "run log"
    s = d - np.median(d, axis=2)[:, :, None]
    su = np.median(s.max(axis=2), axis=0)
    giu = np.median(-s.min(axis=2), axis=0)
    return np.where(su > giu, 1.0, -1.0), "data"


def analizza(path, live, max_eventi):
    """Ampiezze, posizioni, piedistalli e rumore per evento e canale.

    Tutto canale per canale (2026-10-16, PMT e SiPM sulla stessa scheda):
    il verso dal DCOffset, la posizione dell'impulso misurata sugli eventi
    (efficienza.finestre_per_canale), il piedistallo nel tratto prima
    dell'impulso. Le ampiezze sono dalla parte dell'impulso, quindi positive.
    """
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
        if n == 0:
            return hdr, chans, dt, n_tot, amp, pos, dc, rms, np.ones(nch), "", [], a0
        prima = np.asarray(w[a0:min(a0 + 1000, n_tot), :]).reshape(-1, nch, ns).astype(np.float32)
        segni, fonte = versi(path, chans, prima)
        fin = efficienza.finestre_per_canale(prima - np.median(prima, axis=2)[:, :, None],
                                             dt, segni)
        del prima
        for a in range(a0, n_tot, 2000):
            z = min(a + 2000, n_tot)
            d = np.asarray(w[a:z, :]).reshape(z - a, nch, ns).astype(np.float32)
            i, j = a - a0, z - a0
            for k in range(nch):
                fk = fin[k]
                # piedistallo prima dell'impulso; se non c'e' spazio, la
                # mediana della traccia intera, come prima
                tratto = d[:, k, fk["i0"]:fk["i1"]] if "errore" not in fk else d[:, k, :]
                m = np.median(tratto, axis=1)
                o = segni[k] * (d[:, k, :] - m[:, None])
                dc[i:j, k] = m
                amp[i:j, k] = o.max(axis=1)
                pos[i:j, k] = np.argmax(o, axis=1) * dt
                rms[i:j, k] = 1.4826 * np.median(np.abs(o[:, fk["i0"]:fk["i1"]] if "errore" not in fk
                                                        else o), axis=1)
    finally:
        f.close()
        daqio._cleanup(tmp)
    return hdr, chans, dt, n_tot, amp, pos, dc, rms, segni, fonte, fin, a0


def deriva(x, n=101):
    """Media mobile con clipping: isola la deriva dalla dispersione.

    Su run corte la finestra va accorciata, altrimenti copre tutti i dati e
    restituisce una costante: la deriva risulterebbe zero per costruzione.
    """
    if len(x) < 3:
        return np.zeros(max(len(x), 1))
    n = min(n, max(3, len(x) // 6))
    if n % 2 == 0:
        n += 1
    c = np.median(x)
    x = np.clip(x, c - 8, c + 8)
    pad = np.pad(x, n // 2, mode="edge")
    return np.convolve(pad, np.ones(n) / n, mode="valid")[:len(x)]


def main():  # noqa: C901
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

    hdr, chans, dt, n_tot, amp, pos, dc, rms, segni, fonte, fin, a0_eventi = analizza(
        path, live, args.max_events)
    # La posizione prevista dalla latenza vale solo per il self-trigger; con
    # il trigger esterno il ritardo dipende da cavi e moduli. In ogni caso
    # l'impulso si cerca dove e' stato MISURATO, canale per canale.
    if str(hdr.get("TriggerMode", "")).lower().startswith("self"):
        attesa, finattesa = finestra_attesa(hdr)
        if attesa is not None and attesa < 0:
            print("ATTENZIONE: con questa frequenza e PostTriggerSize il self-trigger mette "
                  "l'impulso a %.0f ns, cioe' PRIMA dell'inizio della finestra" % attesa)
        elif attesa is not None:
            print("posizione prevista dal self-trigger: %.0f ns" % attesa)
    print("verso degli impulsi dal %s: %s" % (fonte, "  ".join(
        "ch%d %s" % (c, "+" if segni[k] > 0 else "-") for k, c in enumerate(chans))))

    def finestra(k):
        fk = fin[k] if fin else {}
        if "j0" in fk:
            return fk["j0"] * dt, (fk["j1"] - 1) * dt
        return -1e9, 1e9
    if n_tot == 0:
        print("eventi: NESSUNO")
        print()
        print("La run gira ma non sta triggerando. Se la soglia qui sopra e' lontana")
        print("dal piedistallo, l'impulso non ci arriva: abbassa l'offset, anche a run")
        print("in corso, senza fermarla:")
        print()
        for c in (st["channels"] if st else []):
            print("    printf '%d 3\\n' > %s"
                  % (c["ch"], os.path.join(args.data_dir, "live-threshold.txt")))
        return
    print("eventi: %d totali, %d analizzati" % (n_tot, len(amp)))
    if len(amp) < 600:
        print("  ATTENZIONE: pochi eventi, la deriva non e' ancora misurabile")
    print()

    buoni_per = []
    for i, ch in enumerate(chans):
        cut = soglia_impulso(np.median(rms[:, i]))
        fa, fb = finestra(i)
        buoni = (amp[:, i] > cut) & (pos[:, i] > fa) & (pos[:, i] < fb)
        buoni_per.append(buoni)
        pur = 100.0 * buoni.mean() if len(buoni) else float("nan")
        dove = ("impulso cercato in %.0f-%.0f ns" % (fa, fb)) if fa > -1e8 else \
               ("impulso non localizzabile (%s)" % fin[i].get("errore", "?"))
        print("ch%d (%s):  rumore %.2f cnt   impulso in posizione nel %.1f%% degli eventi "
              "(soglia %.0f cnt; %s)" % (ch, "positivo" if segni[i] > 0 else "negativo",
                                         np.median(rms[:, i]), pur, cut, dove))
        if buoni.sum() > 20:
            a = amp[buoni, i]
            q = np.percentile(a, [5, 25, 50, 75, 95])
            print("      ampiezza  %.1f cnt = %.2f mV   quartili %.0f/%.0f   5-95%%: %.0f/%.0f"
                  % (q[2], q[2] * CONTEGGIO_MV, q[1], q[3], q[0], q[4]))
        dd = deriva(dc[:, i])
        print("      piedistallo %.2f   deriva %.2f cnt (Output) = %.2f cnt (Transparent)"
              % (dc[:, i].mean(), dd.max() - dd.min(),
                 (dd.max() - dd.min()) / ATT_CONTINUA))

    # La larghezza su ogni canale con abbastanza impulsi: con PMT e SiPM sulla
    # stessa scheda sono due cose diverse (pochi ns contro ~100), e misurarla
    # solo sul canale con piu' impulsi ne avrebbe mostrata una sola.
    f, tmp = daqio.open_file(path, live=live)
    try:
        w = f["events/waveforms"]
        ns = hdr["SamplesPerChannel"]; nch = len(chans)
        # L'indice del primo evento analizzato va preso da analizza(), non
        # ricalcolato dalla lunghezza del file: a run in corso il file e'
        # cresciuto nel frattempo, e si rileggevano eventi diversi, in gran
        # parte senza impulso. Sui PMT della run 0189 dava 3.5 ns invece di 7.
        a0 = a0_eventi
        print()
        for k, ch in enumerate(chans):
            sel = np.where(buoni_per[k])[0][:300]
            if len(sel) < 20:
                continue
            larg = []
            for e in sel:
                tr = np.asarray(w[a0 + e, :]).reshape(nch, ns).astype(np.float32)[k]
                larg.append(larghezza(segni[k] * (tr - dc[e, k]), dt))
            larg = np.array(larg)
            print("ch%d:  larghezza %.2f ns   (10-90%%: %.2f-%.2f, su %d impulsi)"
                  % (ch, np.median(larg), np.percentile(larg, 10),
                     np.percentile(larg, 90), len(larg)))
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
