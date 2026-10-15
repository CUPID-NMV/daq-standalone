#!/usr/bin/env python3
"""
Stima rozza dell'efficienza di una run, canale per canale.

    python3 tools/efficienza.py data/test_52-0V_0162_750Ms_50PT.h5.gz
    python3 tools/efficienza.py FILE --sigma 5

Per ogni canale, sulle forme d'onda FILTRATE con un passa-basso a 50 MHz
(Butterworth del 4o ordine, avanti e indietro), lo stesso dell'analisi
offline in tools/SiPM_Characterization_DC_Study.ipynb: toglie le righe di
rumore a ~98 e ~188 MHz e non tocca l'impulso dei SiPM, che sta sotto ~30 MHz.
Misurato il 2026-10-15 sulle run 0057-0066: sigma_ped vale 4.7, 4.1 e 12.6
conteggi sulle tracce grezze di ch16-18, 1.5 su tutti e tre dopo il filtro.
Il rumore di ch18 e' quasi tutto pick-up sopra i 50 MHz. Filtrare serve anche
a parlare la stessa lingua dell'analisi offline. Il comparatore del
self-trigger invece vede il segnale NON filtrato: per il rumore che arriva al
trigger conta la sigma grezza (--grezzo).

1. il piedistallo di ogni evento e' la mediana dei campioni nella finestra
   10-180 ns, la stessa del monitor: prima dell'impulso, e non la mediana di
   tutta la traccia, che un impulso lungo (SiPM) sposta (CLAUDE.md);
2. sigma_ped e' la larghezza della distribuzione dei campioni in quella
   finestra, piedistallo tolto, sugli eventi del primo blocco: deviazione
   standard con i valori oltre 3.5 sigma esclusi, iterata. Non la MAD: su
   campioni interi la MAD e' intera, e sigma_ped saltava fra 2.97 e 4.45
   conteggi (1.4826 x 2 e x 3) da una run all'altra (2026-10-15);
3. l'ampiezza dell'evento e' l'escursione massima DALLA PARTE DELL'IMPULSO,
   cercata in +-50 ns attorno alla posizione tipica dell'impulso (l'istante
   del trigger), non su tutta la traccia: su un SiPM una traccia di 1 us
   contiene impulsi di buio, e la stima diventava un'occupazione;
4. efficienza = eventi con ampiezza > N sigma_ped / eventi totali (N = 4).

Accanto a questa, due numeri per leggerla:

- il FONDO: la stessa frazione contata in una finestra di solo piedistallo
  larga quanto la ricerca. E' misurato, non estrapolato;
- "eff_tag": la frazione sugli eventi in cui TUTTI gli altri canali superano
  30 mV. Non dipende da com'e' composto il trigger: sulle run del 2026-10-13
  in self-trigger OR la frazione semplice di ch18 era 79%, quella sui tag
  89%, contro 92% e 91.5% delle run in maggioranza 2 del 12.

Quello che la stima NON toglie:

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
from scipy.signal import butter, sosfiltfilt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daqio   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "data", "efficienza")
PED_NS = (10.0, 180.0)
# Fra la fine della finestra del piedistallo e il picco: la salita degli
# impulsi al buio dei SiPM dura ~30 ns (CLAUDE.md), il doppio per sicurezza.
MARGINE_NS = 60.0
MIN_CAMPIONI_PED = 20
RICERCA_NS = 50.0          # semiampiezza della finestra di ricerca attorno all'impulso
TAG_MV = 30.0              # soglia sugli ALTRI canali per eff_tag
LP_MHZ = 50.0              # passa-basso: lo stesso del notebook DC_Study
LP_ORDINE = 4
# Il filtro lavora in float64: con 32 canali un blocco da 2000 eventi
# sarebbero 500 MB. Il blocco si dimensiona sul numero di campioni.
CAMPIONI_PER_BLOCCO = 3e7
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


def sigma_robusta(x, k=3.5, giri=5):
    """Deviazione standard dei soli valori entro k sigma, iterata."""
    x = np.asarray(x, dtype=np.float64).ravel()
    m, s = np.median(x), x.std()
    for _ in range(giri):
        sel = np.abs(x - m) < k * s
        if sel.sum() < 10:
            break
        m, s = x[sel].mean(), x[sel].std()
    return max(float(s), 0.3)


def stima(path, sigma_n=SIGMA_N, ped_ns=PED_NS, max_eventi=None, segno=None, filtro=True):
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
        if filtro:
            sos = butter(LP_ORDINE, LP_MHZ * 1e6, btype="low", fs=1e9 / dt, output="sos")
            filtra = lambda x: sosfiltfilt(sos, x, axis=2)
        else:
            filtra = lambda x: x
        blocco = int(max(100, min(BLOCCO, CAMPIONI_PER_BLOCCO // (nch * ns))))

        # Dove sta l'impulso. Con PostTriggerSize alto il self-trigger lo mette
        # presto: il 2026-10-13 a 70% cadeva a 115 ns, DENTRO 10-180, e la
        # stima dava efficienza 0 e pavimento 100%. Si misura con la mediana di
        # tutta la traccia, che non dipende dalla finestra: e' solo per sapere
        # DOVE, non per misurare il piedistallo.
        prova = filtra(np.asarray(ds[0:min(totale, 1000)], dtype=np.float32).reshape(-1, nch, ns))
        prova = prova - np.median(prova, axis=2)[:, :, None]
        if segno is None:
            su = np.median(prova.max(axis=2), axis=0)
            giu = np.median(-prova.min(axis=2), axis=0)
            segni_prova = np.where(su > giu, 1.0, -1.0)
        else:
            segni_prova = np.full(nch, float(segno))
        orientato = segni_prova[None, :, None] * prova
        amp_p = orientato.max(axis=2)
        pos_p = orientato.argmax(axis=2) * dt
        alti = amp_p >= np.median(amp_p, axis=0)[None, :]
        picco_ns = float(np.median(pos_p[alti]))
        del prova, orientato

        ped_dichiarata = tuple(ped_ns)
        ristretta = False
        if picco_ns - MARGINE_NS < ped_ns[1]:
            ped_ns = (ped_ns[0], picco_ns - MARGINE_NS)
            ristretta = True
        i0 = int(np.searchsorted(t, ped_ns[0]))
        i1 = int(np.searchsorted(t, ped_ns[1], "right"))
        lung_ped = i1 - i0
        if lung_ped < MIN_CAMPIONI_PED or i1 >= ns - 5:
            raise daqio.DaqFileError(
                "l'impulso cade a %.0f ns: prima di lui restano %d campioni di piedistallo, "
                "troppo pochi per stimarne la larghezza" % (picco_ns, max(lung_ped, 0)))

        j0 = max(i1, int(np.searchsorted(t, picco_ns - RICERCA_NS)))
        j1 = min(ns, int(np.searchsorted(t, picco_ns + RICERCA_NS, "right")))
        larg = j1 - j0
        # Il fondo si conta su una finestra di solo piedistallo LARGA QUANTO la
        # ricerca; se il piedistallo e' piu' corto (finestra ristretta) si usa
        # quello che c'e', e il risultato lo dice.
        larg_fondo = min(larg, lung_ped)
        tag_soglia = TAG_MV / MV_PER_CONTEGGIO

        sigma = soglia = segni = None
        sopra = np.zeros(nch, dtype=np.int64)
        sopra_fondo = np.zeros(nch, dtype=np.int64)
        n_tag = np.zeros(nch, dtype=np.int64)
        sopra_tag = np.zeros(nch, dtype=np.int64)
        for a in range(0, totale, blocco):
            b = min(totale, a + blocco)
            x = filtra(np.asarray(ds[a:b], dtype=np.float32).reshape(b - a, nch, ns))
            ped = np.median(x[:, :, i0:i1], axis=2)
            corr = x - ped[:, :, None]
            del x
            if sigma is None:
                # La larghezza del piedistallo si prende sul primo blocco: 2000
                # eventi per lung_ped campioni sono centinaia di migliaia di
                # valori, e la MAD su tutti i blocchi costerebbe la run intera
                # in memoria.
                sigma = np.array([sigma_robusta(corr[:, k, i0:i1]) for k in range(nch)])
                soglia = sigma_n * sigma
                if segno is None:
                    su = np.median(corr[:, :, i1:].max(axis=2), axis=0)
                    giu = np.median(-corr[:, :, i1:].min(axis=2), axis=0)
                    segni = np.where(su > giu, 1.0, -1.0)
                else:
                    segni = np.full(nch, float(segno))
            amp = (segni[None, :, None] * corr[:, :, j0:j1]).max(axis=2)
            su = amp > soglia[None, :]
            sopra += su.sum(axis=0)
            fondo = (segni[None, :, None] * corr[:, :, i0:i0 + larg_fondo]).max(axis=2)
            sopra_fondo += (fondo > soglia[None, :]).sum(axis=0)
            if nch >= 2:
                alto = amp > tag_soglia
                for k in range(nch):
                    tag = np.delete(alto, k, axis=1).all(axis=1)
                    n_tag[k] += tag.sum()
                    sopra_tag[k] += (tag & su[:, k]).sum()
            del corr
    finally:
        f.close()
        daqio._cleanup(tmp)

    fuori = {}
    for k, ch in enumerate(canali):
        fuori[str(ch)] = {
            "sigma_ped": round(float(sigma[k]), 3),
            "soglia_conteggi": round(float(soglia[k]), 2),
            "soglia_mv": round(float(soglia[k]) * MV_PER_CONTEGGIO, 3),
            "polarita": int(segni[k]),
            "sopra": int(sopra[k]),
            "eff": round(sopra[k] / float(totale), 5),
            "fondo": round(sopra_fondo[k] / float(totale), 5),
            "n_tag": int(n_tag[k]),
            "eff_tag": round(sopra_tag[k] / float(n_tag[k]), 5) if n_tag[k] else None,
        }
    return {"file": os.path.basename(path), "eventi": int(totale),
            "sigma_n": sigma_n, "ped_ns": [round(x, 1) for x in ped_ns],
            "ped_ns_dichiarata": list(ped_dichiarata), "ped_ristretta": ristretta,
            "picco_ns": round(picco_ns, 1),
            "ricerca_ns": [round(float(t[j0]), 1), round(float(t[j1 - 1]), 1)],
            "fondo_larghezza_ns": round(larg_fondo * dt, 1), "tag_mv": TAG_MV,
            "polarita_da": fonte_segno,
            "filtro": ("passa-basso %g MHz, Butterworth ordine %d, avanti e indietro"
                       % (LP_MHZ, LP_ORDINE)) if filtro else "nessuno",
            "canali": fuori}


class StimaInCorso:
    """La stessa stima, un blocco di eventi per volta: per il monitor.

    Il monitor rilegge a ogni giro la coda del file, e la coda contiene
    eventi gia' visti. Qui ogni evento si conta una volta sola, ricordando
    l'indice assoluto dell'ultimo contato. sigma_ped, polarita' e posizione
    dell'impulso si fissano sul primo blocco abbastanza grande (MIN_EVENTI) e
    restano quelle per tutta la run: una soglia che si muove durante la run
    renderebbe incomparabili i punti del grafico.
    """
    MIN_EVENTI = 300

    def __init__(self, dt_ns, sigma_n=SIGMA_N, ped_ns=PED_NS, filtro=True):
        self.dt = float(dt_ns)
        self.sigma_n = sigma_n
        self.ped_ns = tuple(ped_ns)
        self.filtro = filtro
        self.pronta = False
        self.ultimo = None          # indice assoluto dell'ultimo evento contato
        self.errore = None

    def _filtra(self, x):
        if not self.filtro:
            return x
        sos = butter(LP_ORDINE, LP_MHZ * 1e6, btype="low", fs=1e9 / self.dt, output="sos")
        return sosfiltfilt(sos, x, axis=2)

    def _prepara(self, x, segno):
        nch, ns = x.shape[1], x.shape[2]
        t = np.arange(ns) * self.dt
        prova = x - np.median(x, axis=2)[:, :, None]
        if segno is None:
            su = np.median(prova.max(axis=2), axis=0)
            giu = np.median(-prova.min(axis=2), axis=0)
            self.segni = np.where(su > giu, 1.0, -1.0)
        else:
            self.segni = np.full(nch, float(segno))
        orientato = self.segni[None, :, None] * prova
        amp_p = orientato.max(axis=2)
        alti = amp_p >= np.median(amp_p, axis=0)[None, :]
        self.picco_ns = float(np.median((orientato.argmax(axis=2) * self.dt)[alti]))
        a, b = self.ped_ns
        if self.picco_ns - MARGINE_NS < b:
            b = self.picco_ns - MARGINE_NS
        self.i0 = int(np.searchsorted(t, a))
        self.i1 = int(np.searchsorted(t, b, "right"))
        if self.i1 - self.i0 < MIN_CAMPIONI_PED:
            self.errore = "pulse at %.0f ns: too little pedestal before it" % self.picco_ns
            return
        self.j0 = max(self.i1, int(np.searchsorted(t, self.picco_ns - RICERCA_NS)))
        self.j1 = min(ns, int(np.searchsorted(t, self.picco_ns + RICERCA_NS, "right")))
        self.larg_fondo = min(self.j1 - self.j0, self.i1 - self.i0)
        corr = x - np.median(x[:, :, self.i0:self.i1], axis=2)[:, :, None]
        self.sigma = np.array([sigma_robusta(corr[:, k, self.i0:self.i1]) for k in range(nch)])
        self.soglia = self.sigma_n * self.sigma
        self.pronta = True

    def aggiungi(self, dati, primo, segno=None):
        """Conta gli eventi nuovi di `dati` (n, canali, campioni), il cui primo
        ha indice assoluto `primo`. Ritorna (n, sopra[canali], fondo[canali])
        oppure None se non c'e' niente di nuovo o la stima non e' ancora pronta."""
        n_tot = dati.shape[0]
        if self.ultimo is None:
            if n_tot < self.MIN_EVENTI:
                return None
            x = self._filtra(np.asarray(dati, dtype=np.float32))
            self._prepara(x, segno)
            if not self.pronta:
                return None
            nuovi = x              # il primo blocco si conta tutto
        else:
            da = self.ultimo + 1 - primo
            if da >= n_tot:
                return None
            # Se fra due giri sono arrivati piu' eventi della coda letta, i
            # mancanti non si contano: e' un rapporto, perde statistica non
            # correttezza.
            nuovi = self._filtra(np.asarray(dati[max(da, 0):], dtype=np.float32))
        self.ultimo = primo + n_tot - 1
        corr = nuovi - np.median(nuovi[:, :, self.i0:self.i1], axis=2)[:, :, None]
        amp = (self.segni[None, :, None] * corr[:, :, self.j0:self.j1]).max(axis=2)
        fondo = (self.segni[None, :, None] * corr[:, :, self.i0:self.i0 + self.larg_fondo]).max(axis=2)
        return (corr.shape[0], (amp > self.soglia[None, :]).sum(axis=0),
                (fondo > self.soglia[None, :]).sum(axis=0))


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
    chiave = {"versione": 4, "mtime": os.path.getmtime(path), "sigma_n": kw.get("sigma_n", SIGMA_N),
              "filtro": kw.get("filtro", True),
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
    ap.add_argument("--grezzo", action="store_true", help="senza il passa-basso a 50 MHz")
    a = ap.parse_args()
    r = stima(a.file, sigma_n=a.sigma, max_eventi=a.max_eventi, filtro=not a.grezzo)
    print("%s: %d eventi, soglia %.1f sigma_ped, polarita' dal %s"
          % (r["file"], r["eventi"], r["sigma_n"], r["polarita_da"]))
    print("impulso a %.0f ns, piedistallo in %.0f-%.0f ns%s"
          % (r["picco_ns"], r["ped_ns"][0], r["ped_ns"][1],
             "  (RISTRETTA: l'impulso cadeva nella finestra dichiarata)" if r["ped_ristretta"] else ""))
    print("ricerca in %.0f-%.0f ns, fondo su %.0f ns di solo piedistallo; filtro: %s"
          % (r["ricerca_ns"][0], r["ricerca_ns"][1], r["fondo_larghezza_ns"], r["filtro"]))
    print("%4s %4s %9s %17s %9s %8s %7s %18s" % ("ch", "pol", "sigma_ped", "soglia", "sopra", "eff",
                                                 "fondo", "eff_tag (n tag)"))
    for ch, c in r["canali"].items():
        print("%4s %4s %6.2f cn %5.1f cn %5.2f mV %9d %7.1f%% %6.2f%% %8s (%6d)"
              % (ch, "+" if c["polarita"] > 0 else "-", c["sigma_ped"], c["soglia_conteggi"],
                 c["soglia_mv"], c["sopra"], 100 * c["eff"], 100 * c["fondo"],
                 "%.1f%%" % (100 * c["eff_tag"]) if c["eff_tag"] is not None else "-", c["n_tag"]))


if __name__ == "__main__":
    main()
