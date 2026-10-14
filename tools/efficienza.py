#!/usr/bin/env python3
"""
Stima rozza dell'efficienza di una run, canale per canale.

    python3 tools/efficienza.py data/test_52-0V_0162_750Ms_50PT.h5.gz
    python3 tools/efficienza.py FILE --sigma 5

Per ogni canale:

1. il piedistallo di ogni evento e' la mediana dei campioni nella finestra
   10-180 ns, la stessa del monitor: prima dell'impulso, e non la mediana di
   tutta la traccia, che un impulso lungo (SiPM) sposta (CLAUDE.md);
2. sigma_ped e' la larghezza della distribuzione dei campioni in quella
   finestra, piedistallo tolto, su tutti gli eventi del primo blocco:
   1.4826 x MAD, che un picco occasionale non gonfia;
3. l'ampiezza dell'evento e' l'escursione massima DALLA PARTE DELL'IMPULSO,
   cercata dopo la finestra del piedistallo;
4. efficienza = eventi con ampiezza > N sigma_ped / eventi totali (N = 4).

Quello che questa stima NON toglie, da sapere prima di leggerla:

- il rumore da solo supera N sigma in una frazione degli eventi, perche'
  l'ampiezza e' il massimo su centinaia di campioni: e' un pavimento, non zero.
  Per questo il risultato riporta anche lo stesso conteggio fatto su una
  finestra di SOLO piedistallo lunga quanto la ricerca, che lo misura;
- i picchi spuri del V1742, simultanei su tutti i canali (CLAUDE.md), contano
  come impulsi;
- e' l'efficienza di un canale sugli eventi che la DAQ ha registrato, non
  sugli impulsi veri: con il trigger dato da un altro canale e' la frazione
  di coincidenze, con il self-trigger dello stesso canale e' ~1 per
  costruzione.
"""
import argparse
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daqio   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "data", "efficienza")
PED_NS = (10.0, 180.0)
SIGMA_N = 4.0
BLOCCO = 2000            # eventi letti per volta: 32 canali x 1024 campioni x 4 byte = 260 MB
MV_PER_CONTEGGIO = 1000.0 / 4096


def polarita_dal_log(path_log):
    """+1 impulsi positivi, -1 negativi, None se il log non lo dice.

    PulsePolarity e' quello che la DAQ ha configurato: CAEN_DGTZ_PulsePolarity
    vale 0 per positivo e 1 per negativo. E' una dichiarazione, quindi vince
    sulla deduzione dai dati.
    """
    try:
        testo = open(path_log, errors="replace").read()
    except OSError:
        return None
    m = re.search(r"PulsePolarity\s*=\s*(\d)", testo)
    if not m:
        return None
    return 1 if m.group(1) == "0" else -1


def stima(path, sigma_n=SIGMA_N, ped_ns=PED_NS, max_eventi=None, segno=None):
    """{"run", "eventi", "sigma_n", "ped_ns", "canali": {ch: {...}}} per un file."""
    if segno is None:
        base = path[:-3] if path.endswith(".gz") else path
        segno = polarita_dal_log(os.path.splitext(base)[0] + ".log")
    fonte_segno = "run log" if segno is not None else "data"

    f, tmp = daqio.open_file(path)
    try:
        hdr = daqio.read_header(f)
        if "/events/waveforms" not in f:
            raise daqio.DaqFileError("formato v1: la stima vuole il formato v2")
        ds = f["/events/waveforms"]
        canali = [int(c) for c in hdr["ChannelList"]]
        nch = len(canali)
        ns = int(hdr["SamplesPerChannel"])
        dt = float(hdr["SamplingTime"]) * 1e9
        totale = ds.shape[0] if not max_eventi else min(ds.shape[0], max_eventi)
        if totale == 0:
            raise daqio.DaqFileError("nessun evento")
        t = np.arange(ns) * dt
        i0 = int(np.searchsorted(t, ped_ns[0]))
        i1 = int(np.searchsorted(t, ped_ns[1], "right"))
        lung_ped = i1 - i0
        if lung_ped < 5 or i1 >= ns - 5:
            raise daqio.DaqFileError("finestra del piedistallo %s ns fuori dalla traccia" % (ped_ns,))

        sigma = soglia = segni = None
        sopra = np.zeros(nch, dtype=np.int64)
        sopra_ped = np.zeros(nch, dtype=np.int64)
        for a in range(0, totale, BLOCCO):
            b = min(totale, a + BLOCCO)
            x = np.asarray(ds[a:b], dtype=np.float32).reshape(b - a, nch, ns)
            ped = np.median(x[:, :, i0:i1], axis=2)
            corr = x - ped[:, :, None]
            del x
            if sigma is None:
                # La larghezza del piedistallo si prende sul primo blocco: 2000
                # eventi per lung_ped campioni sono centinaia di migliaia di
                # valori, e la MAD su tutti i blocchi costerebbe la run intera
                # in memoria.
                finestra = corr[:, :, i0:i1].transpose(1, 0, 2).reshape(nch, -1)
                sigma = 1.4826 * np.median(np.abs(finestra), axis=1)
                sigma = np.maximum(sigma, 0.5)    # sotto mezzo conteggio e' quantizzazione
                soglia = sigma_n * sigma
                if segno is None:
                    su = np.median(corr[:, :, i1:].max(axis=2), axis=0)
                    giu = np.median(-corr[:, :, i1:].min(axis=2), axis=0)
                    segni = np.where(su > giu, 1.0, -1.0)
                else:
                    segni = np.full(nch, float(segno))
            amp = (segni[None, :, None] * corr[:, :, i1:]).max(axis=2)
            sopra += (amp > soglia[None, :]).sum(axis=0)
            # Il pavimento: lo stesso conteggio su tratti di solo piedistallo
            # lunghi quanto la ricerca. La finestra 10-180 e' piu' corta della
            # ricerca, quindi si estrapola: un massimo su L campioni di rumore
            # indipendente supera la soglia con P_L = 1 - (1 - p)^L, e p si
            # misura dal campione per campione nella finestra.
            sopra_ped += (segni[None, :, None] * corr[:, :, i0:i1] > soglia[None, :, None]).sum(axis=(0, 2))
            del corr
    finally:
        f.close()
        daqio._cleanup(tmp)

    lung_ricerca = ns - i1
    fuori = {}
    for k, ch in enumerate(canali):
        p = sopra_ped[k] / float(totale * lung_ped)
        pavimento = 1.0 - (1.0 - p) ** lung_ricerca
        fuori[str(ch)] = {
            "sigma_ped": round(float(sigma[k]), 3),
            "soglia_conteggi": round(float(soglia[k]), 2),
            "soglia_mv": round(float(soglia[k]) * MV_PER_CONTEGGIO, 3),
            "polarita": int(segni[k]),
            "sopra": int(sopra[k]),
            "eff": round(sopra[k] / float(totale), 5),
            # Stima, non misura: suppone campioni di rumore indipendenti, e
            # l'oscillazione a ~98 MHz vista sui SiPM li rende correlati.
            "pavimento_rumore": round(float(pavimento), 5),
        }
    return {"file": os.path.basename(path), "eventi": int(totale),
            "sigma_n": sigma_n, "ped_ns": list(ped_ns), "ricerca_campioni": int(lung_ricerca),
            "polarita_da": fonte_segno, "canali": fuori}


def nome_run(path):
    base = os.path.basename(path)
    for suff in (".h5.gz", ".h5"):
        if base.endswith(suff):
            return base[:-len(suff)]
    return base


def stima_in_cache(path, **kw):
    """Come stima(), ma ricorda il risultato in data/efficienza/<run>.json.

    La stima legge la run intera e su un file compresso deve prima
    decomprimerlo: decine di secondi a run. Il riepilogo serale la chiede per
    tutte le run del giorno, il controllore la fa a fine run: la cache evita di
    rifarla, e si invalida se il file cambia o cambiano i parametri.
    """
    os.makedirs(CACHE, exist_ok=True)
    cache = os.path.join(CACHE, nome_run(path) + ".json")
    chiave = {"mtime": os.path.getmtime(path), "sigma_n": kw.get("sigma_n", SIGMA_N),
              "ped_ns": list(kw.get("ped_ns", PED_NS))}
    try:
        with open(cache) as f:
            r = json.load(f)
        if r.get("chiave") == chiave:
            return r
    except (OSError, ValueError):
        pass
    r = stima(path, **kw)
    r["chiave"] = chiave
    tmp = cache + ".tmp"
    with open(tmp, "w") as f:
        json.dump(r, f, indent=1)
    os.replace(tmp, cache)
    return r


def file_della_run(path_log):
    """Il file dati di una run dal suo log, compresso o no, oppure None."""
    base = os.path.splitext(path_log)[0]
    for p in (base + ".h5.gz", base + ".h5"):
        if os.path.exists(p):
            return p
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--sigma", type=float, default=SIGMA_N, help="soglia in sigma_ped (4)")
    ap.add_argument("-n", "--max-eventi", type=int, default=None)
    a = ap.parse_args()
    r = stima(a.file, sigma_n=a.sigma, max_eventi=a.max_eventi)
    print("%s: %d eventi, soglia %.1f sigma_ped, polarita' dal %s"
          % (r["file"], r["eventi"], r["sigma_n"], r["polarita_da"]))
    print("%4s %4s %9s %11s %9s %8s %9s" % ("ch", "pol", "sigma_ped", "soglia", "sopra", "eff",
                                              "pavimento"))
    for ch, c in r["canali"].items():
        print("%4s %4s %6.2f cn %5.1f cn %4.2f mV %9d %7.1f%% %8.2f%%"
              % (ch, "+" if c["polarita"] > 0 else "-", c["sigma_ped"], c["soglia_conteggi"],
                 c["soglia_mv"], c["sopra"], 100 * c["eff"], 100 * c["pavimento_rumore"]))


if __name__ == "__main__":
    main()
