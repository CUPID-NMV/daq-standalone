"""
Lettura dei file HDF5 prodotti da DAQ-WC.

Gestisce trasparentemente:
  - i due formati di output
      v1: un dataset per evento, /events/event0, /events/event1, ...
      v2: un unico dataset estendibile /events/waveforms  (n_eventi, n_campioni)
  - i file compressi (.h5.gz)
  - la lettura a run in corso, via SWMR (solo formato v2)

Uso tipico, anche da notebook:

    from daqio import load
    hdr, data = load("data/run.h5.gz")     # data: (n_eventi, n_canali, n_campioni)
    for i, ch in enumerate(hdr["ChannelList"]):
        ...
"""

import gzip
import os
import shutil
import tempfile

import numpy as np
import h5py


__all__ = ["load", "read_header", "open_file", "DaqFileError",
           "baseline_amplitude", "count_events"]


class DaqFileError(RuntimeError):
    """Errore di lettura di un file di dati della DAQ."""


# ----------------------------------------------------------------------

def open_file(path, live=False):
    """Apre il file HDF5. Ritorna (File, path_temporaneo_o_None).

    Con live=True usa SWMR per leggere mentre la run e' in corso; richiede un
    file scritto con LiveMonitoring = true (formato v2).
    """
    tmp = None

    if path.endswith(".gz"):
        if live:
            raise DaqFileError("Un file .gz e' gia' chiuso: --live non ha senso.")
        tmp = tempfile.NamedTemporaryFile(suffix=".h5", delete=False).name
        with gzip.open(path, "rb") as fin, open(tmp, "wb") as fout:
            shutil.copyfileobj(fin, fout)
        path = tmp

    try:
        if live:
            return h5py.File(path, "r", libver="latest", swmr=True), tmp
        return h5py.File(path, "r"), tmp

    except BlockingIOError:
        _cleanup(tmp)
        raise DaqFileError(
            f"Il file '{path}' e' bloccato: la run e' ancora in corso.\n"
            "Rilancia con --live (richiede LiveMonitoring = true nel TOML)."
        )
    except OSError as exc:
        _cleanup(tmp)
        if live:
            raise DaqFileError(
                f"Impossibile aprire '{path}' in modalita' SWMR.\n  ({exc})\n\n"
                "Il file e' stato probabilmente scritto con LiveMonitoring = false,\n"
                "oppure con una versione della DAQ precedente al formato v2.\n"
                "In quel caso i dati sono leggibili solo a run terminata."
            )
        raise DaqFileError(f"Impossibile leggere '{path}': {exc}")


def _cleanup(tmp):
    if tmp and os.path.exists(tmp):
        os.unlink(tmp)


# ----------------------------------------------------------------------

def read_header(f):
    """Attributi di /config, con byte-string decodificate."""
    if "/config" not in f:
        raise DaqFileError("Nessun gruppo /config: non e' un output di DAQ-WC.")

    hdr = {}
    for k, v in f["/config"].attrs.items():
        if isinstance(v, bytes):
            v = v.decode()
        elif isinstance(v, np.ndarray) and v.dtype.kind == "S":
            v = [x.decode() for x in v]
        hdr[k] = v

    # I file v1 non dichiarano la versione del formato
    hdr.setdefault("FormatVersion", 1 if "/events/waveforms" not in f else 2)
    return hdr


def _read_v2(f, hdr, max_events, live, last=None):
    ds = f["/events/waveforms"]
    if live:
        ds.refresh()          # senza refresh si vede solo lo stato all'apertura

    total = ds.shape[0]
    if total == 0:
        raise DaqFileError("Il file non contiene ancora eventi.")

    if last:
        # Solo la coda: il monitor si aggiorna a ritmo costante anche su run
        # lunghe, invece di rileggere tutto il file a ogni giro.
        n_ev = min(total, last)
        flat = np.asarray(ds[total - n_ev:total], dtype=np.float64)
    else:
        n_ev = min(total, max_events) if max_events else total
        flat = np.asarray(ds[:n_ev], dtype=np.float64)

    return flat, n_ev, total


def _read_v1(f, hdr, max_events, live, last=None):
    if live:
        raise DaqFileError(
            "La lettura a run in corso richiede il formato v2.\n"
            "Questo file usa il formato storico (un dataset per evento), che non\n"
            "e' compatibile con SWMR."
        )
    group = f["/events"]
    keys = sorted(group.keys(), key=lambda x: int(x.replace("event", "")))
    if not keys:
        raise DaqFileError("Il file non contiene eventi.")
    total = len(keys)
    if last:
        keys = keys[-last:]
    elif max_events:
        keys = keys[:max_events]

    flat = np.stack([np.asarray(group[k][:], dtype=np.float64) for k in keys])
    return flat, len(keys), total


def load(path, max_events=None, live=False, last=None):
    """Carica un file di dati.

    Ritorna (header, data) con data di forma (n_eventi, n_canali, n_campioni).
    `last` legge solo gli ultimi N eventi, utile per il monitoraggio dal vivo:
    il costo resta costante anche mentre il file cresce. Il numero totale di
    eventi presenti nel file finisce comunque in hdr["NEventsInFile"].
    """
    f, tmp = open_file(path, live=live)
    try:
        hdr = read_header(f)
        reader = _read_v2 if hdr["FormatVersion"] >= 2 else _read_v1
        flat, n_ev, total = reader(f, hdr, max_events, live, last)
    finally:
        f.close()
        _cleanup(tmp)

    n_ch = len(hdr["ChannelList"])
    width = flat.shape[1]

    if width % n_ch != 0:
        raise DaqFileError(
            f"Larghezza evento ({width}) non divisibile per il numero di canali "
            f"({n_ch}): forse un gruppo del digitizer era assente."
        )

    n_samp = width // n_ch
    # SamplesPerChannel esiste solo dal formato v2; sui file v1 si deduce
    declared = int(hdr.get("SamplesPerChannel", n_samp))
    if declared != n_samp:
        raise DaqFileError(
            f"Incoerenza: /config dichiara {declared} campioni per canale, "
            f"ma i dati ne contengono {n_samp}."
        )
    hdr.setdefault("SamplesPerChannel", n_samp)
    hdr.setdefault("TailCut", int(hdr.get("RecordLength", n_samp)) - n_samp)
    hdr["NEventsInFile"] = int(total)

    return hdr, flat.reshape(n_ev, n_ch, n_samp)


# ----------------------------------------------------------------------
#  Analisi di base, condivisa fra gli strumenti
# ----------------------------------------------------------------------

def baseline_amplitude(data):
    """(baseline, corr, amp, noise) per un array (n_eventi, n_canali, n_campioni).

    Il piedistallo e' la mediana dell'INTERA traccia, non del tratto iniziale:
    gli impulsi occupano poche decine di campioni su oltre mille, quindi non la
    spostano, e la stima resta valida anche con PostTriggerSize basso, quando la
    finestra pre-impulso e' troppo corta per contenere solo baseline. Con
    PostTriggerSize = 10 a 2.5 GS/s l'impulso cade a ~50 ns, cioe' dentro il
    primo 15% della traccia: stimando li' il piedistallo si ottiene un rumore
    gonfiato di un fattore 4 e ampiezze sottostimate.

    Il rumore si stima con la MAD riscalata, che per la stessa ragione e'
    insensibile agli impulsi.
    """
    base = np.median(data, axis=2)
    corr = data - base[:, :, None]
    noise = np.median(np.abs(corr), axis=2) * 1.4826

    hi = corr.max(axis=2)
    lo = corr.min(axis=2)
    amp = np.where(np.abs(lo) > np.abs(hi), lo, hi)

    return base, corr, amp, noise


def count_events(path, live=True):
    """Numero di eventi nel file, 0 se non ce ne sono ancora.

    Legge solo la forma del dataset, senza caricare le forme d'onda. Serve per
    seguire la crescita di una run: usare load() per questo significava
    sollevare un'eccezione proprio all'avvio, quando il file e' ancora vuoto --
    che con il segnale spento e' la norma, non un caso limite.
    """
    f, tmp = open_file(path, live=live)
    try:
        if "/events/waveforms" in f:
            ds = f["/events/waveforms"]
            if live:
                ds.refresh()
            return int(ds.shape[0])
        if "/events" in f:
            return len(f["/events"].keys())
        return 0
    finally:
        f.close()
        _cleanup(tmp)
