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
    """Il verso degli impulsi dichiarato dal log della run, oppure None.

    Prima il DCOffset di ogni canale ("ch24: DCOffset = 0x7000"), che vale
    canale per canale: con PMT negativi e SiPM positivi sulla stessa scheda e'
    l'unica dichiarazione giusta per tutti (verso_da_dcoffset). I canali nella
    zona di mezzo prendono PulsePolarity. Ritorna {canale: +1/-1}.

    Se il log non riporta i DCOffset, PulsePolarity (0 positivo, 1 negativo),
    uguale per tutti: ritorna +1 o -1.
    """
    try:
        testo = open(path_log, errors="replace").read()
    except OSError:
        return None
    m = re.search(r"PulsePolarity\s*=\s*(\d)", testo)
    globale = (1 if m.group(1) == "0" else -1) if m else None
    dco = re.findall(r"ch(\d+): DCOffset = (0x[0-9A-Fa-f]+)", testo)
    if dco:
        fuori = {}
        for c, v in dco:
            verso = verso_da_dcoffset(int(v, 16))
            verso = verso if verso is not None else globale
            if verso is not None:
                fuori[int(c)] = verso
        return fuori or None
    return globale


def verso_da_dcoffset(v):
    """+1 sopra 0x9000, -1 sotto 0x8000, None in mezzo.

    In mezzo il piedistallo sta a meta' dinamica e lascia spazio da tutte e
    due le parti: il DCOffset non dichiara niente. Le run dei SiPM fino al
    2026-10-15 erano a 0x8000 con impulsi positivi.
    """
    if v > 0x9000:
        return 1
    if v < 0x8000:
        return -1
    return None


def _segni_per(canali, segno):
    """Array di versi per i canali dati, da un dict, uno scalare o None."""
    if segno is None:
        return None
    if isinstance(segno, dict):
        if not all(c in segno for c in canali):
            return None
        return np.array([float(segno[c]) for c in canali])
    return np.broadcast_to(np.asarray(segno, dtype=float), (len(canali),)).copy()


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


def finestre_per_canale(prova, dt, segni, ped_ns=PED_NS):
    """Finestre di piedistallo, ricerca e fondo, UNA PER CANALE.

    `prova` sono eventi filtrati col piedistallo della traccia intera tolto
    (serve solo a sapere DOVE sta l'impulso, e non dipende da nessuna
    finestra). Per ogni canale, la forma d'onda media del 20% di eventi piu'
    alti -- orientata col verso del canale -- ha il picco dove sta l'impulso.
    Il 20% e non la meta': i PMT hanno un impulso in ~un evento su quattro, e
    la meta' piu' alta conteneva soprattutto rumore.

    Per canale e non comune, dal 2026-10-16: con PMT e SiPM sulla stessa
    scheda gli impulsi cadevano a ~150 e ~290 ns, e una finestra comune
    cercava i PMT dove non c'erano. Un canale senza impulsi (picco medio sotto
    5 volte il rumore) prende la posizione mediana dei canali dello stesso
    verso che li hanno.

    Ritorna una lista di dict con i0, i1 (piedistallo), j0, j1 (ricerca),
    larg_fondo, picco_ns, ristretta; oppure con "errore" se il piedistallo
    prima dell'impulso e' troppo corto.
    """
    nch, ns = prova.shape[1], prova.shape[2]
    t = np.arange(ns) * dt
    picchi, inizi, ha_impulsi = [], [], []
    for k in range(nch):
        x = segni[k] * prova[:, k, :]
        amp = x.max(axis=1)
        sel = amp >= np.percentile(amp, 80)
        m = x[sel].mean(axis=0) if sel.sum() >= 5 else x.mean(axis=0)
        p = int(np.argmax(m))
        a_ = p
        while a_ > 0 and m[a_] > 0.1 * m[p]:
            a_ -= 1
        rumore = 1.4826 * float(np.median(np.abs(x - np.median(x))))
        picchi.append(float(t[p]))
        inizi.append(float(t[a_]))
        ha_impulsi.append(float(m[p]) > 5 * max(rumore, 0.5))
    for k in range(nch):
        if not ha_impulsi[k]:
            altri = [picchi[j] for j in range(nch) if ha_impulsi[j] and segni[j] == segni[k]] \
                or [picchi[j] for j in range(nch) if ha_impulsi[j]]
            if altri:
                picchi[k] = float(np.median(altri))
                vicini = [inizi[j] for j in range(nch) if ha_impulsi[j] and picchi[j] in altri]
                inizi[k] = float(np.median(vicini)) if vicini else picchi[k] - MARGINE_NS
    fuori = []
    for k in range(nch):
        picco = picchi[k]
        a, b = ped_ns
        ristretta = False
        # Il piedistallo finisce 20 ns prima di dove l'impulso COMINCIA (10%
        # del picco), non a una distanza fissa dal picco: i PMT salgono in
        # pochi ns, i SiPM in ~70, e un margine unico di 60 ns dal picco
        # lasciava i PMT senza piedistallo a PostTrigger 70%.
        fine = inizi[k] - 20.0
        if fine < b:
            b, ristretta = fine, True
        i0 = int(np.searchsorted(t, a))
        i1 = int(np.searchsorted(t, b, "right"))
        if i1 - i0 < MIN_CAMPIONI_PED or i1 >= ns - 5:
            fuori.append({"errore": "pulse at %.0f ns: %d samples of pedestal before it"
                          % (picco, max(i1 - i0, 0)), "picco_ns": picco})
            continue
        j0 = max(i1, int(np.searchsorted(t, picco - RICERCA_NS)))
        j1 = min(ns, int(np.searchsorted(t, picco + RICERCA_NS, "right")))
        fuori.append({"i0": i0, "i1": i1, "j0": j0, "j1": j1, "picco_ns": picco,
                      "larg_fondo": min(j1 - j0, i1 - i0), "ristretta": ristretta,
                      "ha_impulsi": ha_impulsi[k]})
    return fuori


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
        segno = _segni_per(canali, segno)
        if segno is None:
            fonte_segno = "data"
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
            segni_prova = segno
        fin = finestre_per_canale(prova, dt, segni_prova, ped_ns)
        del prova
        validi = [k for k in range(nch) if "errore" not in fin[k]]
        if not validi:
            raise daqio.DaqFileError(fin[0]["errore"])
        tag_soglia = TAG_MV / MV_PER_CONTEGGIO
        segni = segni_prova

        sigma = soglia = None
        sopra = np.zeros(nch, dtype=np.int64)
        sopra_fondo = np.zeros(nch, dtype=np.int64)
        n_tag = np.zeros(nch, dtype=np.int64)
        sopra_tag = np.zeros(nch, dtype=np.int64)
        for a in range(0, totale, blocco):
            b = min(totale, a + blocco)
            x = filtra(np.asarray(ds[a:b], dtype=np.float32).reshape(b - a, nch, ns))
            m = b - a
            amp = np.full((m, nch), -np.inf)
            fondo = np.full((m, nch), -np.inf)
            if sigma is None:
                # La larghezza del piedistallo si prende sul primo blocco: 2000
                # eventi per la lunghezza del piedistallo sono centinaia di
                # migliaia di valori.
                sigma = np.full(nch, np.nan)
            for k in validi:
                w = fin[k]
                corr = x[:, k, :] - np.median(x[:, k, w["i0"]:w["i1"]], axis=1)[:, None]
                if np.isnan(sigma[k]):
                    sigma[k] = sigma_robusta(corr[:, w["i0"]:w["i1"]])
                amp[:, k] = (segni[k] * corr[:, w["j0"]:w["j1"]]).max(axis=1)
                fondo[:, k] = (segni[k] * corr[:, w["i0"]:w["i0"] + w["larg_fondo"]]).max(axis=1)
            del x
            soglia = sigma_n * sigma
            su = amp > soglia[None, :]
            sopra += su.sum(axis=0)
            sopra_fondo += (fondo > soglia[None, :]).sum(axis=0)
            if nch >= 2:
                alto = amp > tag_soglia
                for k in validi:
                    altri = [j for j in validi if j != k]
                    if not altri:
                        continue
                    tag = alto[:, altri].all(axis=1)
                    n_tag[k] += tag.sum()
                    sopra_tag[k] += (tag & su[:, k]).sum()
    finally:
        f.close()
        daqio._cleanup(tmp)

    fuori = {}
    for k, ch in enumerate(canali):
        w = fin[k]
        if "errore" in w:
            fuori[str(ch)] = {"errore": w["errore"], "picco_ns": round(w["picco_ns"], 1),
                              "polarita": int(segni[k]), "eff": None, "fondo": None,
                              "eff_tag": None, "n_tag": 0}
            continue
        fuori[str(ch)] = {
            "picco_ns": round(w["picco_ns"], 1),
            "ped_ns": [round(float(t[w["i0"]]), 1), round(float(t[w["i1"] - 1]), 1)],
            "ped_ristretta": w["ristretta"],
            "ricerca_ns": [round(float(t[w["j0"]]), 1), round(float(t[w["j1"] - 1]), 1)],
            "ha_impulsi": w["ha_impulsi"],
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
            "sigma_n": sigma_n, "ped_ns_dichiarata": list(ped_ns),
            "ped_ristretta": any(fuori[str(c)].get("ped_ristretta") for c in canali),
            "finestre": "per channel", "tag_mv": TAG_MV,
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
            self.segni = np.broadcast_to(np.asarray(segno, dtype=float), (nch,)).copy()
        self.fin = finestre_per_canale(prova, self.dt, self.segni, self.ped_ns)
        self.validi = [k for k in range(nch) if "errore" not in self.fin[k]]
        if not self.validi:
            self.errore = self.fin[0]["errore"]
            return
        self.sigma = np.full(nch, np.nan)
        for k in self.validi:
            w = self.fin[k]
            corr = x[:, k, :] - np.median(x[:, k, w["i0"]:w["i1"]], axis=1)[:, None]
            self.sigma[k] = sigma_robusta(corr[:, w["i0"]:w["i1"]])
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
        m, nch = nuovi.shape[0], nuovi.shape[1]
        sopra = np.zeros(nch, dtype=np.int64)
        fondo = np.zeros(nch, dtype=np.int64)
        for k in self.validi:
            w = self.fin[k]
            corr = nuovi[:, k, :] - np.median(nuovi[:, k, w["i0"]:w["i1"]], axis=1)[:, None]
            sopra[k] = ((self.segni[k] * corr[:, w["j0"]:w["j1"]]).max(axis=1) > self.soglia[k]).sum()
            fondo[k] = ((self.segni[k] * corr[:, w["i0"]:w["i0"] + w["larg_fondo"]]).max(axis=1)
                        > self.soglia[k]).sum()
        return (m, sopra, fondo)


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
    chiave = {"versione": 5, "mtime": os.path.getmtime(path), "sigma_n": kw.get("sigma_n", SIGMA_N),
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
    print("finestre per canale; filtro: %s" % r["filtro"])
    print("%4s %4s %9s %13s %13s %9s %17s %9s %8s %7s %16s" % (
        "ch", "pol", "picco", "piedistallo", "ricerca", "sigma_ped", "soglia", "sopra", "eff",
        "fondo", "eff_tag (n tag)"))
    for ch, c in r["canali"].items():
        if c.get("errore"):
            print("%4s %4s %6.0f ns   non stimabile: %s" % (
                ch, "+" if c["polarita"] > 0 else "-", c["picco_ns"], c["errore"]))
            continue
        print("%4s %4s %6.0f ns %5.0f-%-4.0f ns%s %5.0f-%-4.0f ns %6.2f cn %5.1f cn %5.2f mV %9d %7.1f%% %6.2f%% %8s (%6d)"
              % (ch, "+" if c["polarita"] > 0 else "-", c["picco_ns"], c["ped_ns"][0], c["ped_ns"][1],
                 "*" if c["ped_ristretta"] else " ", c["ricerca_ns"][0], c["ricerca_ns"][1],
                 c["sigma_ped"], c["soglia_conteggi"], c["soglia_mv"], c["sopra"], 100 * c["eff"],
                 100 * c["fondo"],
                 "%.1f%%" % (100 * c["eff_tag"]) if c["eff_tag"] is not None else "-", c["n_tag"]))
    if r["ped_ristretta"]:
        print("* piedistallo accorciato: l'impulso del canale cadeva nella finestra dichiarata")


if __name__ == "__main__":
    main()
