#!/usr/bin/env python3
"""
Monitor online con i grafici dei segnali, servito via HTTP.

Avvia un piccolo server sulla macchina DAQ che rigenera i grafici dagli
ultimi eventi acquisiti e li mostra in una pagina che si aggiorna da sola.
Pensato per l'uso con VS Code Remote-SSH, che inoltra la porta sul locale.

    python3 tools/live_monitor.py              # porta 8765, ultimo file in data/
    python3 tools/live_monitor.py -p 9000 -n 30

Poi apri http://localhost:8765 nel browser.

Usa solo la libreria standard piu' numpy/h5py/matplotlib: niente Flask.
"""

import argparse
import errno
import glob
import io
import json
import os
import sys
import collections
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import numpy as np

import matplotlib
matplotlib.use("Agg")          # nessun display: si generano solo PNG
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daqio
from daqio import load, baseline_amplitude, DaqFileError


def _mila(n):
    """1234567 -> '1 234 567'. Su uno spettro da centomila eventi la cifra
    nuda si legge male proprio quando serve confrontarla con mille."""
    return f"{int(n):,}".replace(",", "\u202f")


# ----------------------------------------------------------------------
#  Stato condiviso
# ----------------------------------------------------------------------

class Monitor:
    """Tiene l'ultimo set di dati letto e ne calcola rate e statistiche.

    I dati vengono riletti al massimo ogni `min_interval` secondi: il server
    e' multi-thread e senza questa cache ogni immagine richiesta dalla pagina
    farebbe una rilettura completa del file.
    """

    # Range di ingresso del V1742: 1 Vpp di serie, 2 Vpp con l'opzione
    # VPERS1742. L'ADC e' a 12 bit, quindi il passo in mV segue da qui.
    def mv_per_count(self):
        return 1000.0 * self.vpp / 4096.0

    # Il primo percentile su poche centinaia di eventi e' di fatto il secondo
    # valore piu' piccolo e fluttua di +-8 conteggi fra un aggiornamento e
    # l'altro. Serve qualche migliaio di eventi perche' converga.
    MIN_EVENTS_FOR_THRESHOLD = 1000

    TAGLIO_CODA = 20      # campioni finali scartati: vedi analysis()
    OVERVIEW_EVENTS = 200 # quanti eventi bastano alle mediane della panoramica

    def __init__(self, data_dir, path=None, max_events=2000, min_interval=2.0,
                 vpp=1.0):
        self.data_dir = data_dir
        self.fixed_path = path
        self.max_events = max_events
        self.min_interval = min_interval
        self.vpp = vpp

        self.lock = threading.Lock()      # matplotlib non e' thread-safe
        self.hdr = None
        self.data = None
        self.tail_cut = self.TAGLIO_CODA
        self.sel_channels = None      # None = tutti quelli del file
        self.att_forzata = None       # attenuazione imposta, invece che dedotta
        self.origin = 0               # primo evento assoluto da considerare
        self._ov = None               # panoramica: (istante, risultato)
        self.overview_interval = 5.0  # si aggiorna al massimo ogni 5 s
        self.error = None
        self.path = None

        self.n_events = 0
        self.rate = 0.0
        self.start_time = None

        self.status = None        # live-status.json pubblicato dalla DAQ
        self.segno = -1.0         # verso dell'impulso, finche' non ci sono dati
        # Finestra in cui si misura il piedistallo, in ns. Prima dell'impulso
        # e non dal campione zero: i primi campioni dopo la cella di trigger
        # del DRS4 non sono puliti.
        self.baseline_ns = (10.0, 180.0)
        self.base_finestra = None     # com'e' stata applicata davvero

        # Spettri cumulati dall'inizio della run, uno per grandezza
        # ("cariche", "ampiezze"). Vedi accumula().
        self.cumulati = {}
        self.primo_evento = 0     # indice assoluto del primo evento in memoria
        self.gen = None           # generazione delle soglie gia' vista
        self.offsets = {}         # offset correnti, per canale
        self.frozen = {}          # cariche prima dell'ultimo cambio di soglia
        self.frozen_offsets = {}  # offset a cui si riferiscono
        self._ana = None          # analisi gia' calcolata per questo refresh
        self._last_read = 0.0
        self._prev = None                 # (n_eventi, timestamp)
        # Storia (timestamp, eventi totali) per ricavare il rate su un numero
        # fisso di eventi invece che sull'intervallo fra due aggiornamenti: a
        # rate basso due aggiornamenti possono non contenere alcun evento, e il
        # numero mostrato sarebbe zero pur stando acquisendo.
        self._hist = collections.deque(maxlen=4000)
        self._ultimo_conteggio = -1       # per capire se il file cresce ancora
        self._cresciuto = 0.0             # ultimo istante in cui e' cresciuto

    # -- lettura -------------------------------------------------------

    def _latest_file(self):
        if self.fixed_path:
            return self.fixed_path
        files = glob.glob(os.path.join(self.data_dir, "*.h5"))
        if not files:
            raise DaqFileError(f"Nessun file .h5 in {os.path.abspath(self.data_dir)}")
        return max(files, key=os.path.getmtime)

    # NOTA: si cercano solo i .h5 non compressi. A run finita il file diventa
    # .h5.gz e non e' piu' monitorabile dal vivo: e' il comportamento voluto,
    # il monitor segue la run in corso.

    def _read_status(self):
        """live-status.json, scritto dalla DAQ. Assente = nessuna informazione."""
        path = os.path.join(self.data_dir, "live-status.json")
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def azzera(self):
        """Riparte da adesso: scarta gli eventi gia' acquisiti e la storia.

        Il monitor non accumula dati propri -- rilegge sempre la coda del file
        -- ma gli istogrammi contengono comunque tutto cio' che e' stato preso
        prima, e dopo un cambio di condizioni quella parte sporca il confronto.
        Si segna il numero d'evento attuale e da li' in avanti si guarda solo
        cio' che arriva.
        """
        self.origin = int(self.n_events or 0)
        # Anche gli spettri cumulati: "azzera" vuol dire ripartire da adesso,
        # e un cumulato che sopravvive all'azzeramento direbbe il contrario di
        # quello che la pagina promette.
        self.cumulati = {}
        self._hist.clear()
        self.frozen = {}
        self.frozen_offsets = {}
        self._ana = None
        self._ov = None
        self._prev = None
        self.rate = 0.0

    def set_baseline_ns(self, a, b):
        """Finestra del piedistallo. Cambiarla invalida l'analisi in memoria."""
        nuova = (a, b)
        if nuova == self.baseline_ns:
            return
        self.baseline_ns = nuova
        self._ana = None
        # I cumulati sono stati calcolati con l'altro piedistallo: sommarli ai
        # nuovi darebbe uno spettro che non corrisponde a nessuna misura.
        # Vale per le cariche come per le ampiezze.
        self.cumulati = {}

    def select_channels(self, channels):
        """Limita i grafici di dettaglio a questi canali (None = tutti).

        Cambiarli invalida i dati in memoria: sono stati letti per un altro
        insieme di canali e non descrivono piu' quello richiesto.
        """
        if channels == self.sel_channels:
            return
        self.sel_channels = channels
        self.data = None
        self._ana = None
        self._last_read = 0.0

    def overview(self):
        """Statistiche per canale su TUTTI i canali del file.

        Con molti canali la domanda non e' piu' "che forma ha il segnale" ma
        "quali canali sono vivi, quali rumorosi, quali morti". Si legge un
        canale per volta, cosi' la memoria non dipende da quanti sono, e su
        pochi eventi, perche' per delle mediane bastano.
        """
        now = time.time()
        if self._ov and (now - self._ov[0]) < self.overview_interval:
            return self._ov[1]
        try:
            path = self._latest_file()
        except DaqFileError:
            return None
        try:
            f, tmp = daqio.open_file(path, live=True)
        except DaqFileError:
            return None
        try:
            hdr = daqio.read_header(f)
            ds = f["/events/waveforms"]
            ds.refresh()
            tot = ds.shape[0]
            if tot == 0:
                return None
            ns = int(hdr["SamplesPerChannel"])
            chans = [int(c) for c in hdr["ChannelList"]]
            # Anche la panoramica riparte dall'azzeramento: legge dal file per
            # conto suo, quindi senza questo continuerebbe a mostrare eventi
            # che i grafici hanno gia' scartato.
            disponibili = max(tot - self.origin, 0)
            if disponibili == 0:
                return None
            n = min(disponibili, self.OVERVIEW_EVENTS)
            a = tot - n
            # Verso dell'impulso, come in _verso(): prima quello dichiarato
            # dalla DAQ, poi -- se non c'e' self-trigger non esiste
            # live-status.json -- quello che dicono i dati, canale per canale.
            pol = (self.status or {}).get("polarity")
            verso_dich = 1.0 if pol == "rising" else (-1.0 if pol == "falling" else None)

            righe = []
            for i, ch in enumerate(chans):
                blocco = np.asarray(ds[a:tot, i * ns:(i + 1) * ns], dtype=np.float32)
                if self.tail_cut:
                    blocco = blocco[:, :max(blocco.shape[1] - self.tail_cut, 1)]
                base = np.median(blocco, axis=1)
                sig = blocco - base[:, None]
                rms = float(1.4826 * np.median(np.abs(sig)))

                # Si guarda l'escursione dalla parte DELL'IMPULSO. Qui era
                # cablato sul minimo, cioe' sui segnali negativi dei PMT: con
                # un SiPM positivo nessun evento superava la soglia, e la
                # panoramica mostrava rate e ampiezza a zero su un canale che
                # stava acquisendo a centinaia di hertz. Sembrava un grafico
                # rotto, ed era una convenzione sbagliata.
                verso = verso_dich
                if verso is None:
                    su = float(np.median(sig.max(axis=1)))
                    giu = float(np.median(-sig.min(axis=1)))
                    verso = 1.0 if su > giu else -1.0
                amp = verso * (sig.max(axis=1) if verso > 0 else sig.min(axis=1))
                taglio = max(10.0, 8.0 * rms)
                ok = amp > taglio
                righe.append({
                    "ch": ch,
                    "gruppo": ch // 8,
                    "baseline": float(np.median(base)),
                    "rms": rms,
                    "occupazione": float(ok.mean()),
                    "ampiezza": float(np.median(amp[ok])) if ok.sum() > 2 else 0.0,
                    # L'ampiezza SENZA taglio. Serve quando il taglio a 8 rms
                    # non lo passa nessuno: un canale con impulsi veri ma
                    # piccoli -- un SiPM a 5 rms dal rumore -- risultava
                    # altrimenti identico a un canale morto, con due barre a
                    # zero. Il grafico le distingue tratteggiando questa.
                    "ampiezza_tutti": float(np.median(amp)),
                    "taglio": float(taglio),
                })
        finally:
            f.close()
            daqio._cleanup(tmp)
        self._ov = (now, {"eventi": n, "canali": righe})
        return self._ov[1]

    def refresh(self, force=False):
        now = time.time()
        if not force and (now - self._last_read) < self.min_interval:
            return
        self._last_read = now

        try:
            path = self._latest_file()
            # Solo la coda del file: il costo di un aggiornamento non deve
            # crescere con la durata della run.
            hdr, data = load(path, last=self.max_events, live=True,
                             channels=self.sel_channels)
        except DaqFileError as exc:
            self.error = str(exc).splitlines()[0]
            return
        except Exception as exc:                      # pragma: no cover
            self.error = f"{type(exc).__name__}: {exc}"
            return

        self.error = None

        # Cambio di file: si riparte da zero con tutto cio' che e' storia.
        #
        # Succede quando la run finisce: il file diventa .h5.gz, qui si cercano
        # solo i .h5, e si ripiega sul piu' recente rimasto -- che puo' essere
        # di giorni prima. Senza azzerare, il rate veniva calcolato fra il
        # conteggio della run finita e quello del file vecchio e usciva
        # NEGATIVO: -30.37 Hz, visto davvero.
        if path != self.path:
            self._prev = None
            self._hist.clear()
            self.rate = 0.0
            self.origin = 0
            self.cumulati = {}
            self._ana = None
            self.status = None

        self.path = path
        total = int(hdr.get("NEventsInFile", data.shape[0]))

        # Il file cresce ancora? E' la differenza fra "sto guardando la run in
        # corso" e "sto guardando un file fermo", che sulla pagina erano
        # indistinguibili: stessi grafici, stessi numeri, nessun avviso.
        if self._ultimo_conteggio != total:
            self._ultimo_conteggio = total
            self._cresciuto = now

        if self._prev is not None:
            prev_n, prev_t = self._prev
            dt = now - prev_t
            if dt > 0 and total >= prev_n:
                self.rate = (total - prev_n) / dt
        self._prev = (total, now)
        if not self._hist or total != self._hist[-1][1]:
            self._hist.append((now, total))

        self.start_time = hdr.get("StartTime") or None
        self.n_events = total
        self.hdr = hdr
        # Indice assoluto del primo evento letto: serve sia per scartare quelli
        # precedenti all'azzeramento, sia piu' sotto per distinguere gli eventi
        # presi prima e dopo l'ultimo cambio di soglia.
        primo = total - data.shape[0]
        if self.origin > primo:
            data = data[self.origin - primo:]
            primo = self.origin
        self.data = data
        self.primo_evento = primo
        self._ana = None          # ricalcolata sotto, una volta sola

        res = self.analysis()
        if res is None:
            return
        _, _, _, amp, _, _, q = res

        st = self._read_status()
        # Un nome vuoto o assente significa "non ancora noto", non "altra run":
        # scartare lo stato lascerebbe in mostra gli offset di una run passata,
        # che e' l'errore peggiore fra i due.
        name = (st or {}).get("file") or ""
        if st and name and name != os.path.basename(path):
            st = None
        self.status = st

        if st is None:
            return

        gen = st.get("generation")
        changed_at = int(st.get("changed_at_event", 0))
        channels = [int(c) for c in hdr["ChannelList"]]

        # Indice assoluto di ogni evento della coda, per sapere quali sono stati
        # acquisiti prima e quali dopo l'ultimo cambio di soglia.
        first = total - data.shape[0]
        is_new = (np.arange(first, total) >= changed_at)

        if gen != self.gen:
            # Congela la distribuzione precedente: senza questo, scorrendo la
            # coda gli eventi vecchi sparirebbero e il confronto con loro.
            #
            # Si prende dal CUMULATO, non dai soli eventi in memoria: quello
            # contiene tutto cio' che e' stato acquisito con la soglia di
            # prima, non gli ultimi due secondi, e il confronto fra il prima e
            # il dopo e' il motivo per cui questa roba esiste. Se il cumulato
            # e' vuoto -- monitor appena acceso -- si ripiega sugli eventi in
            # memoria precedenti al cambio, come si faceva.
            dep = self.cumulati.get("ampiezze", {}).get("val", {})
            if dep:
                self.frozen = {ch: v.copy() for ch, v in dep.items()}
                self.frozen_offsets = dict(self.offsets)
            elif (~is_new).any():
                self.frozen = {ch: q[~is_new, i].copy()
                               for i, ch in enumerate(channels)}
                self.frozen_offsets = dict(self.offsets)
            self.gen = gen

        self.offsets = {int(c["ch"]): c.get("offset")
                        for c in st.get("channels", [])}

    # -- analisi -------------------------------------------------------

    def rate_last(self, n=100):
        """Rate sugli ultimi n eventi, dalla storia dei conteggi.

        Si cerca il campione piu' recente in cui i totali erano almeno n
        indietro: la differenza di tempo e' quella in cui quegli n eventi sono
        arrivati. Se non ce ne sono ancora n, si usa tutto quello che c'e'.
        """
        if len(self._hist) < 2:
            return None
        t_now, n_now = self._hist[-1]
        bersaglio = n_now - n
        for t, k in reversed(self._hist):
            if k <= bersaglio:
                return (n_now - k) / (t_now - t) if t_now > t else None
        t0, k0 = self._hist[0]
        return (n_now - k0) / (t_now - t0) if t_now > t0 else None

    def rate_avg(self):
        """Rate medio dall'inizio della run, se il file dice quando e' iniziata.

        Il monitor puo' essere stato avviato a run gia' in corso, quindi la sua
        prima osservazione non e' l'inizio: senza StartTime nel file si ripiega
        su quella, e lo si dichiara.
        """
        if not self._hist:
            return None, ""
        t_now, n_now = self._hist[-1]
        t0 = self.start_time
        if t0:
            return (n_now / (t_now - t0), "") if t_now > t0 else (None, "")
        t0, n0 = self._hist[0]
        if t_now <= t0:
            return None, ""
        return (n_now - n0) / (t_now - t0), " (dal monitor)"

    ATTENUAZIONE = {"2.5GHz": 16.1, "1GHz": 12.8}   # misurate su impulsi da 1.6 ns
    # (gli impulsi dei PMT sono poi risultati 1.80 ns: vedi larghezza_pmt_20260930)

    def attenuazione(self):
        """Quanto il Transparent Mode riduce un impulso stretto.

        E' il fattore che lega l'ampiezza nella forma d'onda registrata a
        quella che vede il comparatore del self-trigger, cioe' che permette di
        leggere lo spettro nelle stesse unita' della soglia. Misurato su
        impulsi da 1.6 ns, la larghezza degli impulsi dei PMT: per impulsi
        larghi vale molto meno (1.9 a 100 ns) e questa conversione non varrebbe.
        """
        if self.att_forzata:
            return self.att_forzata
        return self.ATTENUAZIONE.get(str((self.hdr or {}).get("SamplingRate", "")), 16.1)

    def attenuazione_misurata(self):
        """Vero se per QUESTA frequenza l'attenuazione e' stata misurata.

        A 750 MHz non lo e': senza questo, il grafico mostrerebbe una scala in
        millivolt ricavata dal valore di 2.5 GS/s e nessuno saprebbe che e'
        inventata. La calibrazione dipende dalla frequenza del 29% fra 2.5 e
        1 GS/s, quindi non e' un dettaglio.
        """
        if self.att_forzata:
            return True
        return str((self.hdr or {}).get("SamplingRate", "")) in self.ATTENUAZIONE

    def analysis(self):
        """(hdr, base, corr, amp, t_ns) oppure None se non ci sono ancora dati."""
        if self.data is None or self.data.shape[0] == 0:
            return None
        if self._ana is not None:
            return self._ana

        # Gli ultimi campioni della finestra portano spesso un picco positivo
        # spurio del V1742. Scartarli prima di qualsiasi calcolo: altrimenti
        # sporcano piedistallo e rumore, non solo l'aspetto del grafico.
        d = self.data
        if self.tail_cut:
            d = d[:, :, :max(d.shape[2] - self.tail_cut, 1)]
        base, corr, amp, noise = baseline_amplitude(d)
        self.segno = self._verso(corr)

        dt_ns = float(self.hdr.get("SamplingTime", 1e-9)) * 1e9
        t_ns = np.arange(d.shape[2]) * dt_ns

        # Piedistallo misurato in una finestra DICHIARATA, prima dell'impulso,
        # evento per evento.
        #
        # daqio prende la mediana dell'intera traccia: giusta per gli impulsi
        # dei PMT, larghi pochi nanosecondi su oltre mille campioni, sbagliata
        # per un SiPM con il LED che ne occupa 400 su 1321 e tira dentro la
        # mediana la sua stessa coda. Misurato: mediana di tutta la traccia
        # 275.0 conteggi, dei soli campioni pre-impulso 271.0. I quattro
        # conteggi di differenza, integrati, valgono piu' del segnale.
        #
        # La finestra si dichiara invece di dedurla: una rilevazione
        # automatica dell'inizio impulso che avevo provato scattava
        # sull'offset stesso che doveva correggere. Qui chi guarda sa dove
        # viene presa, la vede scritta sull'asse, e la sposta se la
        # configurazione cambia -- a 5 GHz la finestra intera dura 205 ns e
        # 10-180 non e' piu' un tratto pulito.
        self.base_finestra = None
        a, b = self.baseline_ns
        if a is not None and b is not None and b > a:
            i0 = int(np.searchsorted(t_ns, a))
            i1 = int(np.searchsorted(t_ns, b, "right"))
            i1 = min(i1, d.shape[2])
            if i1 - i0 >= 5:
                off = np.median(corr[:, :, i0:i1], axis=2)
                corr = corr - off[:, :, None]
                base = base + off
                # Il rumore si rimisura li' dentro: la MAD sull'intera traccia
                # comprende l'impulso e lo conta come fluttuazione.
                noise = 1.4826 * np.median(
                    np.abs(corr[:, :, i0:i1]), axis=2)
                hi, lo = corr.max(axis=2), corr.min(axis=2)
                amp = np.where(np.abs(lo) > np.abs(hi), lo, hi)
                self.base_finestra = (float(t_ns[i0]), float(t_ns[i1 - 1]), i1 - i0)
        # L'ampiezza dell'impulso e' l'escursione dalla parte DELL'IMPULSO,
        # non la maggiore in valore assoluto: quella faceva entrare nello
        # spettro anche i picchi di rumore dal lato sbagliato come se fossero
        # segnale. Da che parte sia lo dice self.segno -- prima era cablato
        # sui segnali negativi dei PMT, e con un SiPM positivo lo spettro
        # usciva tutto a zero.
        #
        # In unita' di offset, cioe' divisa per l'attenuazione del Transparent
        # Mode, lo spettro si legge nelle stesse unita' della soglia del
        # self-trigger e si vede cosa taglia.
        estremo = corr.max(axis=2) if self.segno > 0 else corr.min(axis=2)
        q = np.maximum(self.segno * estremo, 0.0) / self.attenuazione()
        self._ana = (self.hdr, base, corr, amp, t_ns, noise, q)
        return self._ana

    # Oltre questo numero di cariche per canale si smette di accumulare. A
    # 500 Hz sono piu' di un'ora di run, e il grafico lo dichiara invece di
    # mostrare di nascosto uno spettro che ha smesso di crescere.
    MAX_CARICHE = 2_000_000

    def accumula(self, nome, valori, channels, chiave):
        """Aggiunge a un deposito cumulato i valori degli eventi non ancora visti.

        Serve perche' il monitor rilegge solo la CODA del file -- se no il
        costo di un aggiornamento crescerebbe con la durata della run -- e lo
        spettro di tutta la run non si puo' quindi rileggere: si accumula
        evento per evento. Si tiene l'indice assoluto gia' assorbito, cosi' un
        evento non entra due volte quando due letture si sovrappongono.

        `chiave` e' cio' che DEFINISCE il valore: il cancello per la carica,
        la generazione delle soglie per l'ampiezza. Se cambia si riparte,
        perche' sommare valori calcolati in modi diversi darebbe uno spettro
        che non corrisponde a nessuna misura. Vale anche per il file: una run
        nuova e' un'altra cosa.

        Ritorna il deposito, da cui il grafico prende valori, conteggi e
        quanto si e' perso.
        """
        d = self.cumulati.setdefault(
            nome, {"val": {}, "chiave": None, "fino": 0, "persi": 0})

        if d["chiave"] != (chiave, self.path):
            d.update({"val": {}, "chiave": (chiave, self.path),
                      "fino": self.primo_evento, "persi": 0})

        primo = self.primo_evento
        ultimo = primo + valori.shape[0]
        if ultimo <= d["fino"]:
            return d                                 # gia' visti tutti

        if primo > d["fino"]:
            # La DAQ ha prodotto piu' eventi di quanti ne stia in una lettura:
            # quelli in mezzo non li vedremo mai. Contarli e dirlo e' l'unica
            # cosa onesta -- uno spettro "di tutta la run" che in realta' ne
            # salta dei pezzi, senza avvisare, sarebbe peggio di non averlo.
            d["persi"] += primo - d["fino"]
            d["fino"] = primo

        da = d["fino"] - primo
        for i, ch in enumerate(channels):
            ch = int(ch)
            vecchio = d["val"].get(ch)
            nuovo = np.asarray(valori[da:, i], dtype=np.float32)
            if vecchio is None:
                d["val"][ch] = nuovo
            elif vecchio.size < self.MAX_CARICHE:
                d["val"][ch] = np.concatenate([vecchio, nuovo])
        d["fino"] = ultimo
        return d

    @staticmethod
    def da_evento(d):
        """Primo evento assorbito dal deposito, 0 se non si sa."""
        n = max((v.size for v in d["val"].values()), default=0)
        return max(0, d["fino"] - n)

    def self_trigger_attivo(self):
        """True se la run sta usando il self-trigger del V1742.

        Lo si deduce da live-status.json, che la DAQ pubblica SOLO con
        SelfTrigger = true e che refresh() scarta se appartiene a un'altra
        run. E' quindi una dichiarazione della DAQ sulla run in corso, non una
        lettura del TOML -- che puo' essere gia' stato cambiato per la
        prossima run mentre questa e' ancora in aria.
        """
        return self.status is not None

    def _carica(self, corr, t_ns, gate, channels):
        """Carica per evento e canale, in pC, piu' il deposito cumulato.

        Sta in un metodo e non dentro al disegno perche' la usano in due -- lo
        spettro grande e la miniatura nel muro -- e due copie di questo conto
        scivolerebbero via una dall'altra al primo ritocco. E' il conto che e'
        costato di piu' da far tornare.
        """
        dt_ns = float(t_ns[1] - t_ns[0]) if len(t_ns) > 1 else 1.0

        # Il cancello ha caselle sue e non viene dallo zoom delle forme d'onda:
        # la forma d'onda si guarda intera, la carica si integra dove c'e'
        # l'impulso, e legarle costringeva a ritagliare il grafico per fissare
        # il cancello.
        g0, g1 = gate if gate else (None, None)
        a_i = 0 if g0 is None else int(np.searchsorted(t_ns, g0, "left"))
        b_i = len(t_ns) if g1 is None else int(np.searchsorted(t_ns, g1, "right"))
        a_i = max(0, min(a_i, len(t_ns) - 1))
        b_i = max(a_i + 1, min(b_i, len(t_ns)))
        nscamp = b_i - a_i

        # In picocoulomb: l'integrale della tensione diviso l'impedenza
        # d'ingresso. 1 mV x 1 ns / 50 ohm = 0.02 pC. E' la carica all'INGRESSO
        # del digitizer: per risalire a quella del rivelatore servirebbero
        # guadagno e partitori della catena, che il software non conosce.
        k_pc = dt_ns * self.mv_per_count() / 50.0

        # Il piedistallo e' gia' stato tolto in analysis(), nella finestra
        # dichiarata ed evento per evento, quindi qui si somma e basta. La
        # prova che la sottrazione e' quella giusta: allargando il cancello
        # dalla stessa partenza l'integrale SATURA (37.5, 41.5, 44.0, 45.1,
        # 45.1 pC) invece di calare (32.0, 33.8, 33.3, 30.6, 24.5), e un
        # integrale che cala allargando la finestra su un impulso positivo e'
        # impossibile.
        carica = self.segno * corr[:, :, a_i:b_i].sum(axis=2) * k_pc
        dep = self.accumula("cariche", carica, channels, (a_i, b_i))
        return carica, a_i, b_i, nscamp, k_pc, dep

    def _verso(self, corr):
        """+1 se gli impulsi vanno in su, -1 se vanno in giu'.

        Prima fonte la DAQ, che in live-status.json scrive il fronte di
        discriminazione configurato: e' una dichiarazione, non una deduzione.
        Senza self-trigger quel file non c'e' (per esempio con il solo trigger
        esterno di un driver LED) e allora lo si chiede ai dati, guardando da
        che parte e' piu' grande l'escursione tipica. Su puro rumore le due
        parti si equivalgono e la risposta e' arbitraria, ma li' non c'e'
        nessun impulso di cui sbagliare il verso.
        """
        pol = (self.status or {}).get("polarity")
        if pol == "rising":
            return 1.0
        if pol == "falling":
            return -1.0
        su = float(np.median(corr.max(axis=2)))
        giu = float(np.median(-corr.min(axis=2)))
        return 1.0 if su > giu else -1.0

    def effective_threshold(self, values, rms):
        """(ampiezza minima osservata, avvertimento).

        NON e' la soglia. A parita' di soglia nei registri questo valore varia
        di un fattore 2 fra run diverse, perche' dipende da quanti impulsi
        piccoli contiene il campione e dalla fortuna del campionamento a 30 MHz
        del Transparent Mode, non solo dalla soglia. Serve come riferimento
        visivo di dove comincia la distribuzione, niente di piu'.

        Ampiezza del piu' piccolo impulso che ha fatto scattare il trigger. La
        soglia impostata e' in conteggi Transparent Mode e non e' confrontabile
        con queste tracce, che sono in Output Mode e su scala diversa; il bordo
        inferiore della distribuzione degli eventi triggerati e' invece la
        soglia vera *in questa* scala. Si usa un percentile basso anziche' il
        minimo, troppo sensibile a un singolo evento.

        Quando la soglia scende sotto il rumore la stima perde significato: la
        maggior parte degli eventi non contiene un impulso e l'"ampiezza"
        diventa la massima escursione del rumore, positiva tanto quanto
        negativa. In quel caso si restituisce un avvertimento invece di un
        numero: e' proprio il sintomo che interessa vedere.
        """
        nz  = max(rms, 0.5)
        sel = np.abs(values) > 5 * nz
        if sel.sum() < self.MIN_EVENTS_FOR_THRESHOLD:
            return None, (f"not enough statistics: {sel.sum()} events with a pulse, "
                          f"{self.MIN_EVENTS_FOR_THRESHOLD} are needed")

        # In modo "paired" il trigger di un canale fa acquisire anche l'altro,
        # quindi molti eventi senza impulso sono del tutto normali e non
        # indicano affatto che si stia triggerando sul rumore.
        neg = float(np.mean(values[sel] < 0))
        if 0.25 < neg < 0.75:
            return None, "triggering on noise: inconsistent amplitude sign"

        edge = float(np.percentile(np.abs(values[sel]), 1))
        if edge < 6 * nz:
            return None, "threshold inside the noise: the two populations overlap"

        sign = -1.0 if neg > 0.5 else 1.0
        return sign * edge, None

    def stats(self, dettagli=False):
        out = {
            "file": os.path.basename(self.path) if self.path else None,
            # Dopo un azzeramento il conteggio riparte: mostrare il totale del
            # file contraddirebbe "come se non ci fossero dati prima".
            "events": int(max((self.n_events or 0) - self.origin, 0)),
            "events_file": int(self.n_events or 0),
            "rate": round(self.rate, 2),
            "rate100": (lambda r: round(r, 2) if r else None)(self.rate_last(100)),
            "ratemed": (lambda t: round(t[0], 2) if t[0] else None)(self.rate_avg()),
            "ratemednota": self.rate_avg()[1],
            "error": self.error,
            # Da quanto il file non cresce, e di quando e'. Servono a dire
            # "questa non e' una run in corso": senza, un file fermo di giorni
            # prima si presenta identico a una run viva.
            # Dove cade ogni canale nei muri, in frazioni dell'immagine: la
            # pagina ci mette sopra le zone cliccabili. Calcolata qui e non
            # indovinata di la', cosi' se cambia il numero di colonne cambia
            # in un posto solo.
            "muro": None,
            "ferma_da": (round(time.time() - self._cresciuto, 1)
                         if self._cresciuto else None),
            "file_quando": (os.path.getmtime(self.path)
                            if self.path and os.path.exists(self.path) else None),
            "shown": 0,
            "channels": [],
        }
        res = self.analysis()
        if res is None:
            return out

        hdr, base, corr, amp, _, noise, _q = res
        out["shown"] = int(self.data.shape[0])
        out["sampling"] = str(hdr.get("SamplingRate", "?"))
        by_ch = {int(c["ch"]): c for c in (self.status or {}).get("channels", [])}
        canali = [int(c) for c in hdr["ChannelList"]]
        ncol, nrig, celle = geometria_muro(len(canali))
        out["muro"] = {"ncol": ncol, "nrig": nrig,
                       "celle": [{"ch": c, "x": r[0], "y": r[1],
                                  "w": r[2], "h": r[3]}
                                 for c, r in zip(canali, celle)]}

        st_on = self.self_trigger_attivo()
        for i, ch in enumerate(hdr["ChannelList"]):
            rms = float(np.median(noise[:, i]))
            eff, note = ((None, "no self-trigger") if not st_on
                         else self.effective_threshold(amp[:, i], rms))
            info = by_ch.get(int(ch), {})
            out["channels"].append({
                "ch": int(ch),
                "baseline": round(float(np.mean(base[:, i])), 1),
                "rms": round(rms, 2),
                "amp_mean": round(float(np.mean(amp[:, i])), 1),
                # La mediana e' il numero da leggere quando una frazione degli
                # eventi non contiene impulso: quelli tirano la media verso zero
                "amp_med": round(float(np.median(amp[:, i])), 1),
                "amp_med_mv": round(float(np.median(amp[:, i])) * self.mv_per_count(), 2),
                "amp_max": round(float(np.max(np.abs(amp[:, i]))), 1),
                "offset": info.get("offset"),
                "threshold": info.get("threshold"),
                "eff": None if eff is None else round(eff, 1),
                "eff_mv": None if eff is None else round(eff * self.mv_per_count(), 2),
                # L'avvertimento sul perche' non c'e' un'ampiezza minima --
                # "threshold inside the noise", "not enough statistics",
                # "triggering on noise" -- e' roba da messa a punto, come le
                # scritte sui grafici: senza "details" la colonna mostra un
                # trattino e basta. Era rimasto acceso qui perche' la tabella
                # non passava dall'interruttore: stats.json veniva chiesto
                # senza parametri.
                "eff_note": note if dettagli else None,
            })
        return out

    # -- grafici -------------------------------------------------------

    @staticmethod
    def banda_limitata(sig, dt_ns, fc_mhz):
        """Segnale visto attraverso un passa-basso a un polo a fc_mhz.

        Modella la banda della strada del trigger: e' una approssimazione, il
        percorso vero e' piu' complicato, ma riproduce il fenomeno che conta,
        cioe' che un impulso breve perde il picco mentre uno largo no.
        """
        n = sig.shape[-1]
        f = np.fft.rfftfreq(n, d=dt_ns * 1e-9)
        H = 1.0 / (1.0 + 1j * f / (fc_mhz * 1e6))
        return np.fft.irfft(np.fft.rfft(sig) * H, n=n)

    @staticmethod
    def campiona(t_ns, sig, fs_mhz):
        """Istanti in cui un ADC a fs_mhz leggerebbe, e i valori letti.

        E' l'altra meta' del fenomeno: campionare non e' filtrare. Il rumore
        viene letto comunque, l'impulso solo se cade su un istante buono.
        """
        passo = 1e3 / fs_mhz                       # ns fra due campioni
        istanti = np.arange(t_ns[0], t_ns[-1], passo)
        idx = np.searchsorted(t_ns, istanti)
        idx = np.clip(idx, 0, len(t_ns) - 1)
        return t_ns[idx], sig[idx]

    def _figura_panoramica(self, scala="M", dettagli=False):
        """Rate, ampiezza e rumore per ogni canale, raggruppati per gruppo."""
        ov = self.overview()
        if ov is None:
            return self._placeholder()
        righe = ov["canali"]
        mv = self.mv_per_count()
        k = self.SCALE.get(str(scala).upper(), 1.0)

        # L'asse x porta SEMPRE tutti i canali della board, non solo quelli
        # registrati. E' una vista sinottica: serve a dire in un colpo d'occhio
        # quali canali sono vivi, e per farlo il posto di ogni canale deve
        # restare lo stesso fra una run e l'altra. Con le sole barre presenti,
        # registrandone uno il grafico mostrava una barra al centro e basta,
        # e non si sapeva nemmeno quale fosse senza leggere l'etichetta.
        x = [int(r["ch"]) for r in righe]

        # Bassi e stretti: tre strisce da leggere a colpo d'occhio, non tre
        # grafici da studiare. Le colonne dei canali stanno in meno spazio di
        # quanto sembri -- quello che conta e' dove sta la barra, non quanto e'
        # larga.
        fig, axes = plt.subplots(3, 1, sharex=True,
                                 figsize=(LARG_PX * k / DPI_PAN, 2.9 * k))
        wbar = 0.78
        # Le etichette vanno ORIZZONTALI sopra ogni striscia, non ruotate a
        # sinistra: una striscia alta un pollice scarso e' piu' bassa della
        # parola "amplitude [mV]" scritta in verticale, e l'etichetta di un
        # pannello finiva addosso a quella di sotto.
        etichette = ["", "", ""]

        # Bande alternate per gruppo del V1742: sono quattro da otto canali, e
        # con l'asse sempre completo si vedono tutte, anche quelle senza barre.
        for g in range(N_CANALI_HW // 8):
            if g % 2 == 0:
                for ax in axes:
                    ax.axvspan(g * 8 - 0.5, g * 8 + 7.5, color="#000", alpha=.04)
            # Sopra l'etichetta della striscia, non alla stessa altezza:
            # stretta la figura, "occupancy [%]" a sinistra e "gr0" centrato
            # sul primo gruppo finivano uno sull'altro.

        # Il rate per canale non si misura: si ricava dall'occupazione
        # moltiplicata per il rate totale, perche' il trigger e' l'OR dei
        # canali e un evento puo' contenere impulsi su piu' di uno. Se il rate
        # totale non e' ancora noto si ripiega sull'occupazione, dichiarandolo.
        rtot = self.rate_last(100)
        if rtot:
            axes[0].bar(x, [r["occupazione"] * rtot for r in righe],
                        width=wbar, color="#1f77b4")
            etichette[0] = "rate [Hz]"
        else:
            axes[0].bar(x, [100 * r["occupazione"] for r in righe],
                        width=wbar, color="#1f77b4")
            etichette[0] = "occupancy [%]"
            axes[0].set_ylim(0, 105)

        # Dove il taglio non lo passa nessuno si disegna l'ampiezza mediana di
        # TUTTI gli eventi, tratteggiata: dice "c'e' qualcosa, ma sotto il
        # taglio", che e' un'informazione diversa da "non c'e' niente".
        sotto = [r["ampiezza"] == 0.0 for r in righe]
        alt = [abs(r["ampiezza"] if giu is False else r["ampiezza_tutti"]) * mv
               for r, giu in zip(righe, sotto)]
        axes[1].bar([c for c, g in zip(x, sotto) if not g],
                    [v for v, g in zip(alt, sotto) if not g],
                    width=wbar, color="#2ca02c")
        axes[1].bar([c for c, g in zip(x, sotto) if g],
                    [v for v, g in zip(alt, sotto) if g],
                    width=wbar, color="white", edgecolor="#2ca02c",
                    hatch="///", linewidth=1.0)
        etichette[1] = "amplitude [mV]"

        axes[2].bar(x, [r["rms"] * mv for r in righe], width=wbar, color="#d62728")
        etichette[2] = "noise [mV]"
        # I nomi dei gruppi stanno QUI e non sopra le bande. Provati tutti e
        # due i lati sopra la prima striscia: a sinistra sbattevano contro
        # l'etichetta della striscia, a destra pure, perche' le bande coprono
        # tutta la larghezza e un angolo libero su quella riga non esiste.
        # Nell'etichetta dell'asse non possono collidere con niente, e le
        # bande alternate dicono gia' dove finisce un gruppo.
        axes[2].set_xlabel("channel      (gr0: 0-7   \u00b7   gr1: 8-15   "
                           "\u00b7   gr2: 16-23   \u00b7   gr3: 24-31)",
                           fontsize=8.5)

        nota_sotto = any(sotto) and dettagli
        if nota_sotto:
            fig.text(0.5, 0.008,
                     "hatched: no event above the cut \u2014 median amplitude over all events",
                     ha="center", fontsize=8, color="#2ca02c")

        for i_ax, ax in enumerate(axes):
            ax.grid(alpha=.25, axis="y")
            ax.tick_params(labelsize=7)
            ax.set_xlim(-0.8, N_CANALI_HW - 0.2)
            # A DESTRA: a sinistra sbatteva contro "gr0", che sta al centro
            # del primo gruppo e con la figura stretta cade proprio li'. A
            # destra l'ultimo gruppo e' gr3, centrato all'86%, e resta spazio.
            ax.set_title(etichette[i_ax], loc="left", fontsize=7.5,
                         color="#52514e", pad=3)
        axes[2].set_xticks(range(N_CANALI_HW))
        axes[2].set_xticklabels([str(c) for c in range(N_CANALI_HW)], fontsize=6)

        # Il taglio sta nel titolo e non sull'asse y: due righe di etichetta si
        # accavallavano con quella del pannello sotto.
        fig.suptitle("Overview \u00b7 %d of %d channels recorded \u00b7 "
                     "last %d events \u00b7 bars above 8 rms"
                     % (len(righe), N_CANALI_HW, ov["eventi"]), fontsize=9.5)

        fig.tight_layout(rect=(0, 0.035, 1, 1) if nota_sotto else None)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=DPI_PAN)
        plt.close(fig)
        return buf.getvalue()

    # Moltiplicatore delle figure. La dimensione giusta dipende dallo schermo
    # di chi guarda -- un portatile in laboratorio e un monitor grande non
    # vogliono la stessa cosa -- quindi la sceglie la pagina invece di essere
    # indovinata qui dentro.
    SCALE = {"S": 0.78, "M": 1.0, "L": 1.3}

    def figure(self, kind, n_show=1, xlim=(None, None), ylim=(None, None),
               hset=None, bw=None, qcut=None, qset=None, gate=None, scala="M",
               dettagli=False, solo=None):
        if kind == "panoramica":
            # Non passa dall'analisi di dettaglio: quella riguarda i soli
            # canali selezionati e puo' mancare, mentre la panoramica deve
            # poter rispondere comunque.
            return self._figura_panoramica(scala, dettagli)

        res = self.analysis()
        if res is None:
            return self._placeholder()
        hdr, _, corr, amp, t_ns, noise, q = res
        channels = hdr["ChannelList"]
        rect_finale = None
        k = self.SCALE.get(str(scala).upper(), 1.0)

        # "solo" e' un parametro di DISEGNO, non di lettura: dice quale canale
        # mettere nella figura, e non tocca niente di condiviso. La casella
        # "channels" invece e' stato del monitor e cambia i canali LETTI per
        # tutti: usarla per le finestre per canale avrebbe cambiato sotto il
        # naso quello che vede chi guarda la stessa pagina da un'altra
        # macchina, e azzerato i suoi spettri cumulati.
        #
        # I canali si restringono solo per il disegno; gli accumuli e le
        # statistiche continuano a vedere tutto, se no una finestra aperta su
        # un canale farebbe saltare gli eventi agli altri.
        canali_dis = list(channels)
        if solo is not None:
            interi = [int(c) for c in channels]
            if int(solo) in interi:
                canali_dis = [channels[interi.index(int(solo))]]
        idx_dis = [list(channels).index(c) for c in canali_dis]

        def apply_limits(ax):
            """Limiti espliciti dove indicati, autoscale dove no."""
            if xlim[0] is not None or xlim[1] is not None:
                ax.set_xlim(left=xlim[0], right=xlim[1])
            if ylim[0] is not None or ylim[1] is not None:
                ax.set_ylim(bottom=ylim[0], top=ylim[1])

        def etichetta_tempo(extra=None):
            """Dichiara sull'asse quando la vista e' ritagliata.

            Un grafico zoomato e uno i cui dati finiscono davvero li' sono
            altrimenti identici, e la differenza non e' innocua: un asse che
            si ferma a 700 ns su una run a 1 GS/s sembra un errore di
            frequenza di campionamento. E' gia' costato un falso allarme.
            """
            parti = []
            if self.base_finestra:
                parti.append("baseline %.0f-%.0f ns" % self.base_finestra[:2])
            else:
                parti.append("baseline: whole trace")
            if self.tail_cut:
                parti.append(f"last {self.tail_cut} samples dropped")
            if xlim[0] is not None or xlim[1] is not None:
                a = xlim[0] if xlim[0] is not None else float(t_ns[0])
                b = xlim[1] if xlim[1] is not None else float(t_ns[-1])
                parti.append(f"ZOOM {a:.0f}-{b:.0f} ns of the "
                             f"{t_ns[0]:.0f}-{t_ns[-1]:.0f} acquired")
            if extra:
                parti.append(extra)
            return "time [ns]" + (f"      ({' · '.join(parti)})" if parti else "")

        if kind == "waveforms":
            # Piu' larghe di tutte le altre: su una forma d'onda si guarda il
            # tempo, e 200 px in piu' sull'asse x si leggono. Sono l'unica
            # figura che CRESCE in questo riassetto.
            fig, axes = plt.subplots(len(channels), 1,
                                     figsize=(LARG_PX * k / DPI_FIG,
                                              3.3 * k * len(canali_dis)),
                                     squeeze=False, sharex=True)
            n = max(1, min(n_show, corr.shape[0]))
            for j, (i, ch) in enumerate(zip(idx_dis, canali_dis)):
                ax = axes[j][0]
                for e in range(corr.shape[0] - n, corr.shape[0]):
                    ax.plot(t_ns, corr[e, i], lw=0.9 if n == 1 else 0.6,
                            alpha=1.0 if n == 1 else 0.5)
                ax.axhline(0, color="k", lw=0.8, ls=":")

                # La curva filtrata e i campioni a 30 MHz sono un modello
                # della STRADA DEL TRIGGER: la copia attenuata in Transparent
                # Mode su cui il comparatore del self-trigger decide. Con il
                # trigger esterno quella strada non decide niente, e
                # disegnarla sopra la forma d'onda vera vuol dire sovrapporre
                # al dato un modello di qualcosa che non sta succedendo.
                if bw and not self.self_trigger_attivo():
                    pass        # lo si dice sull'asse, piu' sotto
                elif bw:
                    dt = float(t_ns[1] - t_ns[0])
                    grezzo = corr[-1, i]
                    filtrato = self.banda_limitata(grezzo, dt, bw)
                    ts, vs = self.campiona(t_ns, filtrato, 30.0)
                    ax.plot(t_ns, filtrato, lw=1.6, color="#d62728",
                            label=f"after a {bw:g} MHz bandwidth")
                    ax.plot(ts, vs, "o", ms=4, color="#8e44ad", zorder=5,
                            label="samples of a 30 MHz ADC")
                    # Il picco sta dalla parte dell'impulso. Prendendo sempre
                    # il minimo, com'era, su un segnale positivo si misurava
                    # l'escursione negativa del rumore e il rapporto fra i due
                    # "picchi" non voleva dire piu' niente.
                    picco = (lambda v: float(v.max()) if self.segno > 0
                             else float(v.min()))
                    pg, pf = picco(grezzo), picco(filtrato)
                    rg = 1.4826 * np.median(np.abs(grezzo - np.median(grezzo)))
                    rf = 1.4826 * np.median(np.abs(filtrato - np.median(filtrato)))
                    ax.text(0.01, 0.04,
                            f"peak  {pg:.0f} -> {pf:.1f} ADC   (x{pg/pf:.1f})\n"
                            f"noise {rg:.2f} -> {rf:.2f} ADC   (x{rg/rf:.1f})",
                            transform=ax.transAxes, ha="left", va="bottom",
                            fontsize=8.5, color="#333",
                            bbox=dict(fc="white", ec="#ccc", alpha=.85))

                rms = float(np.median(noise[:, i]))
                # Anche questa riga parla del self-trigger: e' l'ampiezza del
                # piu' piccolo impulso che lo ha fatto scattare. Con il
                # trigger esterno non c'e' nessuna selezione in ampiezza,
                # quindi il "bordo" e' solo la fluttuazione piu' piccola
                # capitata, e l'avvertimento "threshold inside the noise"
                # denuncia una soglia che non esiste.
                eff, note = ((None, None) if not self.self_trigger_attivo()
                             else self.effective_threshold(amp[:, i], rms))
                off = self.offsets.get(int(ch))
                if eff is not None:
                    ax.axhline(eff, color="#d62728", lw=1.1, ls="--",
                               label=f"smallest amplitude seen {eff:.0f} ADC"
                                     f"  ({eff * self.mv_per_count():.1f} mV)"
                                     + (f"  offset {off}" if off is not None else ""))
                elif note and dettagli:
                    # Nessuna riga: disegnarne una qui vorrebbe dire inventarsi
                    # un valore che i dati non sostengono. L'avvertimento dice
                    # PERCHE' non c'e', ed e' roba da messa a punto: "not enough
                    # statistics", "triggering on noise", "threshold inside the
                    # noise". Durante la presa dati e' rumore sul grafico.
                    ax.text(0.99, 0.04, note + (f"  (offset {off})" if off is not None else ""),
                            transform=ax.transAxes, ha="right", va="bottom",
                            fontsize=8, color="#d62728")

                # La legenda si disegna una volta sola, alla fine, e solo se
                # c'e' qualcosa da spiegare. Stava dentro il ramo della soglia
                # efficace: con la banda limitata accesa e la soglia non
                # stimabile, le due curve rosse e viola restavano senza
                # didascalia. In alto a destra e dentro il riquadro, dove
                # l'impulso -- che il post-trigger mette nella prima meta'
                # della finestra -- non ci arriva.
                if ax.get_legend_handles_labels()[1]:
                    ax.legend(fontsize=8, loc="upper right", framealpha=.9)

                label = ("last event" if n == 1 else f"last {n} events")
                med = float(np.median(amp[:, i]))
                ax.set_title(f"ch{ch} — {label}   "
                             f"(median amplitude {med:.0f} ADC = "
                             f"{med * self.mv_per_count():.1f} mV)", fontsize=10)
                ax.set_ylabel("ADC − baseline")
                ax.grid(alpha=0.25)
                apply_limits(ax)
            # La spiegazione va sull'ASSE e non dentro il riquadro: li' dentro
            # non c'e' un angolo sicuro -- in basso la trova l'escursione
            # negativa, in alto il picco di un segnale positivo.
            axes[-1][0].set_xlabel(etichetta_tempo(
                "bandwidth model hidden: it describes the self-trigger path, "
                "not used in this run"
                if (bw and not self.self_trigger_attivo() and dettagli) else None))

        elif kind == "average":
            # Nella finestra di un canale la media sta accanto alla forma
            # d'onda, e due riquadri affiancati di altezza diversa si vedono
            # subito: li' usa la stessa altezza della forma d'onda. Sulla
            # pagina resta bassa, dove serve compatta e sta da sola.
            alt = 3.3 if solo is not None else 2.5
            fig, ax = plt.subplots(figsize=(LARG_PX * k / DPI_FIG, alt * k))
            for i, ch in zip(idx_dis, canali_dis):
                line, = ax.plot(t_ns, corr[:, i].mean(axis=0), lw=1.4, label=f"ch{ch}")

                # Anche qui la riga vale solo col self-trigger: e' l'ampiezza
                # del piu' piccolo impulso che lo ha fatto scattare. Era
                # rimasta accesa quando ho tolto le sovrapposizioni dalle forme
                # d'onda, e compariva su una run a trigger esterno.
                if self.self_trigger_attivo():
                    rms = float(np.median(noise[:, i]))
                    eff, _ = self.effective_threshold(amp[:, i], rms)
                    if eff is not None:
                        ax.axhline(eff, color=line.get_color(), lw=1.0, ls="--",
                                   alpha=.7,
                                   label=f"min amp. ch{ch}: {eff:.0f} ADC")
            ax.axhline(0, color="k", lw=0.8, ls=":")
            ax.set_xlabel(etichetta_tempo())
            ax.set_ylabel("ADC − baseline")
            ax.set_title(f"Average over {corr.shape[0]} events", fontsize=10)
            ax.legend()
            ax.grid(alpha=0.25)
            apply_limits(ax)

        elif kind in ("muro_wf", "muro_amp", "muro_car"):
            # Un muro: una miniatura per canale, in griglia. Serve a vedere
            # TUTTI i canali in un colpo d'occhio invece di scorrere N grafici
            # grandi; il dettaglio si apre in una finestra cliccando la cella.
            # Le celle si posizionano a mano con geometria_muro(), cosi' la
            # pagina sa dove cadono e puo' metterci sopra le zone cliccabili.
            ncol, nrig, celle = geometria_muro(len(canali_dis))
            alt = {"muro_wf": 0.95}.get(kind, 0.80)
            fig = plt.figure(figsize=(LARG_PX * k / DPI_FIG,
                                      (alt * nrig + 0.30) * k))

            if kind == "muro_wf":
                # Scala y comune: i canali si confrontano a occhio. Percentili
                # e non min/max, se no un canale con l'impulso grande schiaccia
                # tutti gli altri a una riga piatta.
                lo = float(np.percentile(corr[-1], 0.2))
                hi = float(np.percentile(corr[-1], 99.8))
                titolo = "waveforms \u00b7 last event, same scale \u00b7 click \u2192 window"
            else:
                if kind == "muro_car":
                    carica, a_i, b_i, nscamp, k_pc, dep = self._carica(
                        corr, t_ns, gate, channels)
                    etich = "charge [pC]"
                else:
                    dep = self.accumula("ampiezze", q, channels, self.gen)
                    etich = "amplitude [offset units]"
                dati = {int(c): dep["val"].get(int(c)) for c in canali_dis}
                tutti = np.concatenate([v for v in dati.values()
                                        if v is not None and v.size]) \
                    if any(v is not None and v.size for v in dati.values()) \
                    else np.zeros(1)
                lo = float(np.percentile(tutti, 0.5))
                hi = float(np.percentile(tutti, 99.5))
                if not hi > lo:
                    hi = lo + 1.0
                bordi = np.linspace(lo, hi, 44)
                titolo = "%s \u00b7 same bins \u00b7 click \u2192 window" % etich

            for j, (i, ch) in enumerate(zip(idx_dis, canali_dis)):
                ax = fig.add_axes(celle[j])
                col = PALETTE[j % len(PALETTE)]
                if kind == "muro_wf":
                    ax.plot(t_ns, corr[-1, i], lw=0.7, color=col)
                    ax.axhline(0, color="#bbb", lw=0.5, ls=":")
                    ax.set_ylim(lo, hi)
                    # il fondo scala di QUESTO canale: la scala comune serve a
                    # confrontare, ma da sola nasconde che uno arriva a -120 e
                    # un altro a -9
                    ax.text(0.97, 0.78, "%.0f" % (self.segno * corr[-1, i]).max(),
                            transform=ax.transAxes, fontsize=6.5, color="#888",
                            ha="right")
                else:
                    v = dati.get(int(ch))
                    if v is not None and v.size:
                        ax.hist(v, bins=bordi, color=col, alpha=.85)
                        ax.set_yscale("log")
                    ax.set_xlim(lo, hi)
                ax.text(0.04, 0.78, "ch%d" % int(ch), transform=ax.transAxes,
                        fontsize=7.5, color=col, fontweight="bold")
                ax.set_xticks([]); ax.set_yticks([])
                for sp in ax.spines.values():
                    sp.set_edgecolor("#ddd")

            fig.suptitle(titolo, fontsize=7.5, color="#52514e", y=0.985)
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=DPI_FIG)
            plt.close(fig)
            return buf.getvalue()

        elif kind == "integrals":
            carica, a_i, b_i, nscamp, k_pc, dep_q = self._carica(corr, t_ns, gate,
                                                                 channels)

            # Piu' alto degli altri pannelli: sotto gli assi ci vanno
            # etichetta, legenda e il pie' di pagina, e con 3.4 pollici il
            # grafico si sarebbe schiacciato a una striscia.
            # Meta' della larghezza comune: i due spettri stanno affiancati e
            # insieme fanno la riga, come la panoramica sopra. Con molti canali
            # non si puo' tenere la promessa senza ridurre ogni pannello a una
            # fetta: oltre i due canali la figura cresce e la riga va a capo,
            # che il CSS gia' fa.
            larg_px = max(LARG_PX / 2, 230 * len(canali_dis))
            fig, axes = plt.subplots(1, len(canali_dis),
                                     figsize=(larg_px * k / DPI_FIG, 3.1 * k),
                                     squeeze=False)
            for j, (i, ch) in enumerate(zip(idx_dis, canali_dis)):
                ax = axes[0][j]
                val = dep_q["val"].get(int(ch))
                if val is None or val.size == 0:
                    val = carica[:, i]
                xlo, xhi, logy, nbin = (qset or {}).get(
                    int(ch), (None, None, False, None))

                # I bin si costruiscono DENTRO l'intervallo scelto, non si
                # ritaglia dopo: ritagliando si vedrebbe una fetta di un
                # istogramma calcolato su tutto, con la risoluzione sprecata
                # fuori dalla vista. E' la stessa ragione per cui lo spettro
                # delle ampiezze fa cosi'.
                lo = xlo if xlo is not None else float(val.min())
                hi = xhi if xhi is not None else float(val.max())
                if not hi > lo:
                    hi = lo + 1.0
                est = (lo, hi)

                nb = nbin or min(80, max(20, val.size // 8))
                # Gli stessi bin per le due distribuzioni: con bin diversi il
                # confronto a vista non vorrebbe dire niente, ed e' tutto
                # quello per cui questo grafico esiste.
                bordi = np.linspace(lo, hi, nb + 1)

                # Un solo istogramma, quello di tutta la run. La
                # sovrapposizione degli ultimi mille eventi riscalati c'e'
                # stata e l'abbiamo tolta: con una statistica gia' grande le
                # due curve coincidono a meno del rumore di Poisson del
                # campione piccolo, quindi aggiungeva disturbo e non
                # informazione.
                ax.hist(val, bins=bordi, color=COLORE_CARICA, alpha=.85)
                ax.set_xlim(*est)
                ax.text(0.99, 0.97, "%s events" % _mila(val.size),
                        transform=ax.transAxes, ha="right", va="top",
                        fontsize=8, color="#52514e")

                # Lo zero e' il piedistallo: in uno spettro di carica e' il
                # riferimento che dice se il picco e' segnale o rumore
                # integrato, ed e' il primo controllo da fare.
                ax.axvline(0.0, color="#888", lw=1.2, ls=":")

                # Mediana e larghezza si calcolano su TUTTI gli eventi, anche
                # quelli fuori dalla vista: sono la descrizione della
                # distribuzione, non di cio' che si e' deciso di guardare.
                med = float(np.median(val))

                # La larghezza MISURATA della distribuzione, non quella dedotta
                # dal rumore per campione: su un canale senza segnale questi
                # integrali sono risultati larghi oltre dieci volte la
                # previsione a rumore bianco, perche' il rumore del DRS4 e'
                # correlato fra campioni e la radice di N non vale. La
                # previsione resta accanto, come pavimento: quando la misura la
                # supera di molto, il cancello sta raccogliendo struttura della
                # linea di base e non rumore, e va stretto.
                sparso = 1.4826 * float(np.median(np.abs(val - med)))
                rms = float(np.median(noise[:, i]))
                bianco = rms * np.sqrt(nscamp) * k_pc

                ax.set_title("ch%s \u2014 median %.2f pC" % (ch, med),
                             fontsize=10, pad=16)
                ax.text(0.5, 1.02,
                        ("spread %.2f pC   \u00b7   white-noise floor %.2f pC"
                         % (sparso, bianco)) if dettagli
                        else ("spread %.2f pC" % sparso),
                        transform=ax.transAxes, ha="center", va="bottom",
                        fontsize=9,
                        color="#1a6b1a" if abs(med) > 3 * sparso else "#d62728")

                if logy:
                    ax.set_yscale("log")
                ax.set_xlabel("charge [pC]")
                ax.set_ylabel("events" + (" (log)" if logy else ""))
                ax.grid(alpha=0.25)

            # Cancello, impedenza e verso valgono per tutti i pannelli: scritti
            # su ogni asse si sovrapponevano fra un pannello e l'altro, proprio
            # con piu' canali, che e' il caso normale.
            # Da dove parte il cumulato e quanto se n'e' perso: lo spettro
            # dice "whole run", e va detto quando non e' vero. Capita in due
            # casi -- il monitor acceso a run gia' avviata, e la DAQ che fra
            # due letture produce piu' eventi di quanti ne stia in una.
            da = []
            primo_acc = self.da_evento(dep_q)
            if primo_acc > 0:
                da.append("from event %s" % _mila(primo_acc))
            if dep_q["persi"]:
                # Corto apposta: con quattro canali la figura e' larga, ma con
                # uno solo e' cinque pollici e la riga usciva dai bordi --
                # tagliata proprio sull'avvertimento.
                da.append("%s events missed between reads"
                          % _mila(dep_q["persi"]))
            # Due righe invece di una lunga: con un solo canale la figura e'
            # larga cinque pollici e la riga unica usciva dai bordi, tagliata
            # da entrambe le parti proprio dove c'era l'avvertimento.
            # "charge at the 50 ohm input" era ridondante con l'etichetta
            # dell'asse, che gia' dice "charge [pC]", e a 480 px di larghezza
            # faceva uscire la riga da tutti e due i bordi.
            righe_pie = ["50 \u03a9   \u00b7   gate %.0f-%.0f ns "
                         "(%d samples)   \u00b7   %s pulses"
                         % (t_ns[a_i], t_ns[b_i - 1], nscamp,
                            "positive" if self.segno > 0 else "negative"),
                         (("baseline from %.0f-%.0f ns (%d samples), per event"
                           % self.base_finestra) if self.base_finestra
                          else "baseline from the whole trace: the charge may be biased")]
            if da:
                righe_pie.append("   \u00b7   ".join(da))

            # Il piede dice con quali numeri e' stato fatto il grafico --
            # cancello, impedenza, verso, finestra del piedistallo. Durante una
            # run e' rumore: i numeri li hai appena messi tu. Quando si
            # confrontano due misure invece serve, perche' una carica senza il
            # suo cancello non e' confrontabile con un'altra: lo stesso segnale
            # da' 32 o 45 pC a seconda di dove si integra, misurato. Quindi non
            # sparisce, si accende con "details".
            if dettagli:
                for n_riga, testo in enumerate(reversed(righe_pie)):
                    fig.text(0.5, 0.012 + 0.042 * n_riga, testo, ha="center",
                             fontsize=8.5,
                             color=("#d62728" if (dep_q["persi"] and n_riga == 0
                                                  and len(righe_pie) > 1) else "#555"))
                rect_finale = (0, 0.09 + 0.040 * len(righe_pie), 1, 1)

        else:   # amplitudes
            # Cumulato su tutta la run, come lo spettro di carica: gli eventi
            # in memoria sono solo la coda del file e con una run lunga la
            # distribuzione che si vedeva era quella degli ultimi due secondi.
            # La chiave comprende la generazione delle soglie: cambiando la
            # soglia del self-trigger la popolazione e' un'altra, e sommarle
            # darebbe uno spettro di nessuna delle due. Si riparte da li', e
            # la distribuzione di prima resta accanto in grigio.
            dep_a = self.accumula("ampiezze", q, channels, self.gen)

            larg_px = max(LARG_PX / 2, 230 * len(canali_dis))
            fig, axes = plt.subplots(1, len(canali_dis),
                                     figsize=(larg_px * k / DPI_FIG, 3.1 * k),
                                     squeeze=False)
            for j, (i, ch) in enumerate(zip(idx_dis, canali_dis)):
                ax = axes[0][j]
                values = dep_a["val"].get(int(ch))
                if values is None or values.size == 0:
                    values = q[:, i]

                # Ogni canale ha i propri limiti e la propria scala
                xlo, xhi, logy, nbin = (hset or {}).get(
                    int(ch), (None, None, False, None))

                # Con un intervallo esplicito i bin vanno calcolati dentro quello,
                # altrimenti si vedrebbe solo una fetta di un istogramma costruito
                # su tutto il range e la risoluzione sarebbe sprecata.
                kw = {}
                if xlo is not None or xhi is not None:
                    lo = xlo if xlo is not None else float(np.min(values))
                    hi = xhi if xhi is not None else float(np.max(values))
                    if hi > lo:
                        kw["range"] = (lo, hi)

                # Il taglio fra "prima" e "adesso" non si fa piu' qui: il
                # deposito riparte da solo al cambio di soglia, quindi cio'
                # che contiene e' gia' e solo "adesso".
                old = self.frozen.get(int(ch))
                cur = values

                # I bin devono essere gli stessi per le due distribuzioni,
                # altrimenti il confronto visivo non significa nulla.
                if "range" not in kw:
                    allv = np.concatenate([cur, old]) if old is not None and old.size else cur
                    kw["range"] = (float(np.min(allv)), float(np.max(allv)))
                # Il numero di bin va sul campione complessivo: subito dopo un
                # cambio di soglia gli eventi nuovi sono pochi e l'istogramma
                # risulterebbe grossolano anche per la parte congelata.
                ntot = cur.size + (old.size if old is not None else 0)
                nb = nbin or min(80, max(20, ntot // 8))
                bins = np.linspace(kw["range"][0], kw["range"][1], nb + 1)

                if old is not None and old.size:
                    lo = self.frozen_offsets.get(int(ch))
                    ax.hist(old, bins=bins, color="#888888", alpha=.55,
                            label=f"before (offset {lo})" if lo is not None else "prima")
                cn = self.offsets.get(int(ch))
                ax.hist(cur, bins=bins, color=COLORE_AMPIEZZA, alpha=.85,
                        label=f"now (offset {cn})" if cn is not None else "ora")
                if old is not None and old.size:
                    ax.legend(fontsize=8)

                ax.set_xlim(*kw["range"])
                if logy:
                    ax.set_yscale("log")

                # Quanti eventi ci sono dentro, e da dove: lo spettro dice
                # "tutta la run" e va detto quando non e' vero -- monitor
                # acceso a run avviata, o eventi prodotti fra due letture.
                # Quanti eventi ci sono dentro e che frazione sono della run.
                # Prima la parte mancante era una riga ROSSA a parte -- "37 700
                # missed between reads" -- che sembrava un guasto: non lo e',
                # il monitor legge la coda del file ogni due secondi e a 500 Hz
                # gli eventi in mezzo non li vede mai. Quello che conta per chi
                # guarda e' che lo spettro e' un CAMPIONE, e lo dice la
                # percentuale. Il conteggio dei persi resta, sotto "details".
                conto = "%s events" % _mila(cur.size)
                persi = dep_a["persi"]
                if persi:
                    conto += "   (%.0f%% of the run)" % (
                        100.0 * cur.size / max(1, cur.size + persi))
                ax.text(0.99, 0.97, conto, transform=ax.transAxes,
                        ha="right", va="top", fontsize=8, color="#52514e")
                if persi and dettagli:
                    ax.text(0.99, 0.90, "from %s \u00b7 %s missed between reads"
                            % (_mila(self.da_evento(dep_a)), _mila(persi)),
                            transform=ax.transAxes,
                            ha="right", va="top", fontsize=8, color="#888")

                # Frazione di eventi sotto una soglia in ampiezza. Il default
                # e' l'estremo superiore dell'istogramma, quindi conta tutto:
                # si parte da 100% e si abbassa la soglia per vedere quanta
                # parte dello spettro sta sopra una certa ampiezza.
                # Frazione di eventi sotto una soglia in carica. Il default e'
                # l'estremo superiore dell'istogramma, quindi conta tutto: si
                # parte da 100% e si abbassa la soglia per vedere quanta parte
                # dello spettro sta sopra una certa carica.
                # La soglia in vigore su questo canale, nelle stesse unita'
                # dell'asse: e' il motivo per cui l'asse e' in offset, cioe'
                # vedere quanta parte dello spettro il self-trigger sta
                # tagliando.
                # Questi grafici servono a vedere l'EFFETTO della soglia
                # impostata: la percentuale va quindi calcolata alla soglia in
                # vigore su questo canale, non a un valore scollegato. La
                # casella della pagina resta come scavalcamento, per provare
                # altri valori senza toccare la DAQ.
                mv_off = self.attenuazione() * self.mv_per_count()
                off = self.offsets.get(int(ch)) if self.self_trigger_attivo() else None

                # Riga e percentuale servono a vedere quanta parte dello
                # spettro il self-trigger sta tagliando. Senza self-trigger non
                # c'e' niente da tagliare: prima si ripiegava sull'estremo
                # inferiore dell'istogramma e usciva un "100.0% above cut 1.7"
                # che non dice niente. Resta se la soglia la scrive l'utente
                # nella casella, che e' un "e se la mettessi qui?" legittimo
                # anche a trigger esterno.
                taglio, e_soglia = None, False
                if qcut is not None:
                    taglio = float(qcut)
                elif off is not None:
                    taglio, e_soglia = float(off), True

                if taglio is not None:
                    sopra = (float((cur > taglio).mean()) * 100.0
                             if cur.size else float("nan"))
                    dentro = kw["range"][0] <= taglio <= kw["range"][1]
                    if dentro:
                        ax.axvline(taglio, color="#2ca02c" if e_soglia else "#d62728",
                                   lw=1.4, ls="-" if e_soglia else "--")
                    # La soglia in vigore si disegna comunque, anche quando il
                    # conteggio usa un altro valore: e' il riferimento fisico.
                    if (not e_soglia and off is not None
                            and kw["range"][0] <= off <= kw["range"][1]):
                        ax.axvline(off, color="#2ca02c", lw=1.4)

                    etichetta = ("self-trigger threshold %g" % off) if e_soglia else \
                                ("cut %.1f" % taglio)
                    ax.text(0.5, 1.02, "%.1f%% above %s  (%.0f mV)"
                            % (sopra, etichetta, taglio * mv_off),
                            transform=ax.transAxes, ha="center", va="bottom",
                            fontsize=9, color="#1a6b1a" if e_soglia else "#d62728")
                    if not dentro and dettagli:
                        ax.text(0.01, 0.97, "cut out of range", transform=ax.transAxes,
                                ha="left", va="top", fontsize=8, color="#888")

                # "ADC" da solo e' ambiguo: in questo progetto convivono i
                # conteggi della forma d'onda registrata (questi) e quelli
                # della distanza soglia-piedistallo del self-trigger, che
                # vivono in Transparent Mode e differiscono per l'attenuazione.
                # Quanto vale un'unita' di offset in millivolt all'ingresso:
                # e' l'attenuazione per il passo dell'ADC, cioe' la
                # calibrazione della soglia misurata su impulsi da 1.6 ns; i PMT
                # sono risultati 1.80 ns, il 12% piu' larghi.
                # Su due righe, piu' piccola e con l'avvertimento accorciato:
                # con un canale solo il pannello e' largo cinque pollici e la
                # riga unica usciva da entrambi i bordi, tagliando proprio
                # l'avvertimento sulla calibrazione.
                nota_cal = ("" if self.attenuazione_misurata()
                            else "  \u2014  NOT CALIBRATED here")
                ax.set_xlabel("amplitude [offset units]" +
                              ("\n1 offset = %.2f mV, pulses ~1.8 ns%s"
                               % (mv_off, nota_cal) if dettagli else ""),
                              fontsize=8.5)
                ax.set_ylabel("events" + (" (log)" if logy else ""))
                ax.set_title(f"ch{ch}", fontsize=10, pad=18)
                ax.grid(alpha=0.25)

        fig.tight_layout(rect=rect_finale)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        plt.close(fig)
        return buf.getvalue()

    def _placeholder(self):
        # Uno schermo vuoto dopo un azzeramento sembra un guasto: va detto che
        # si sta aspettando, e da quando.
        if self.error:
            msg = self.error
        elif self.origin:
            msg = ("In attesa di eventi dopo l'azzeramento\n"
                   "(dall'evento %d; nel file ce ne sono %d)"
                   % (self.origin, self.n_events or 0))
        else:
            msg = "In attesa di eventi…"
        fig, ax = plt.subplots(figsize=(9, 2.5))
        ax.text(0.5, 0.5, msg, ha="center", va="center", fontsize=12, wrap=True)
        ax.axis("off")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        plt.close(fig)
        return buf.getvalue()


# ----------------------------------------------------------------------
#  HTTP
# ----------------------------------------------------------------------

PAGE = """<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8">
<title>DAQ V1742 — monitor</title>
<style>
  :root { --bg:#fff; --fg:#1a1a1a; --mut:#666; --line:#e0e0e0; --acc:#0a7; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#16181d; --fg:#e8e8e8; --mut:#999; --line:#2c2f36; --acc:#2db88a; }
  }
  body { background:var(--bg); color:var(--fg); margin:0; padding:20px;
         font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
  h1 { font-size:18px; margin:0 0 4px; }
  .sub { color:var(--mut); font-size:13px; margin-bottom:16px; }
  .bar { display:flex; gap:24px; flex-wrap:wrap; align-items:baseline;
         border:1px solid var(--line); border-radius:8px; padding:12px 16px; margin-bottom:16px; }
  .bar b { font-size:22px; color:var(--acc); font-variant-numeric:tabular-nums; }
  .bar span { color:var(--mut); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
  table { border-collapse:collapse; margin-bottom:20px; font-variant-numeric:tabular-nums; }
  th,td { padding:6px 14px; text-align:right; border-bottom:1px solid var(--line); }
  th { color:var(--mut); font-weight:500; font-size:12px; text-transform:uppercase; }
  td:first-child,th:first-child { text-align:left; }
  .gr { position:relative; display:inline-block; margin-bottom:12px; vertical-align:top; }
  .gr img { margin-bottom:0; }
  .gr .apri { position:absolute; top:7px; right:7px; width:20px; height:20px; line-height:20px;
              text-align:center; border-radius:5px; background:var(--bg); border:1px solid var(--line);
              color:var(--mut); font-size:13px; cursor:pointer; opacity:0; transition:opacity .12s;
              text-decoration:none; }
  .gr:hover .apri { opacity:1; }
  .affianca { display:flex; gap:12px; flex-wrap:wrap; align-items:flex-start; }
  .muro { position:relative; display:inline-block; margin-bottom:10px;
          vertical-align:top; }
  .muro img { margin-bottom:0; display:block; }
  .celle { position:absolute; inset:0; }
  .celle a { position:absolute; border-radius:4px; cursor:pointer;
             border:1px solid transparent; }
  .celle a:hover { border-color:var(--acc); background:rgba(0,170,119,.07); }
  details#interi { margin:4px 0 14px; }
  details#interi summary { cursor:pointer; color:var(--mut); font-size:12px;
                           padding:4px 0; }
  img { max-width:100%; border:1px solid var(--line); border-radius:8px; margin-bottom:16px;
        background:#fff; }
  .err { color:#c33; }
  #alert { display:none; background:#c0392b; color:#fff; padding:12px 16px;
           border-radius:8px; margin-bottom:16px; font-weight:600; line-height:1.45; }
  #upd { color:var(--mut); font-size:12px; }
  /* Dati fermi: le immagini restano quelle dell'ultimo aggiornamento riuscito,
     quindi vanno smorzate per non farle sembrare aggiornate. */
  body.stale img { opacity:.4; filter:grayscale(.5); }
  .ctl { display:flex; gap:16px; flex-wrap:wrap; align-items:flex-end;
         border:1px solid var(--line); border-radius:8px; padding:12px 16px; margin-bottom:16px; }
  .ctl label { display:flex; flex-direction:column; gap:4px; font-size:12px; color:var(--mut); }
  .ctl input { width:82px; padding:5px 7px; font:13px inherit; color:var(--fg);
               background:var(--bg); border:1px solid var(--line); border-radius:5px; }
  .ctl button { padding:6px 14px; font:13px inherit; border-radius:5px; cursor:pointer;
                border:1px solid var(--line); background:var(--bg); color:var(--fg); }
  .ctl .hint { color:var(--mut); font-size:12px; }
  .ctl label.chk { flex-direction:row; align-items:center; gap:6px; font-size:13px;
                   color:var(--fg); padding-bottom:5px; }
  .ctl label.chk input { width:auto; }
  .ctl .grp { font-weight:600; font-size:13px; padding-bottom:5px; min-width:46px; }
</style></head><body>
<div id="alert"></div>
<h1>V1742 DAQ — online monitor</h1>
<div class="sub"><span id="file">…</span> · <span id="upd">waiting for the first update</span></div>
<div class="bar">
  <div><b id="rate100">–</b> <span>Hz · last 100 ev</span></div>
  <div><b id="ratemed">–</b> <span id="ratemednota">Hz · run average</span></div>
  <div><b id="events">–</b> <span>events</span></div>
  <div><b id="shown">–</b> <span>in the plots</span></div>
  <div><span id="err" class="err"></span></div>
</div>
<div class="ctl">
  <label>x min [ns]<input id="xmin" value="__XMIN__" placeholder="auto"></label>
  <label>x max [ns]<input id="xmax" value="__XMAX__" placeholder="auto"></label>
  <label>y min [ADC]<input id="ymin" value="__YMIN__" placeholder="auto"></label>
  <label>y max [ADC]<input id="ymax" value="__YMAX__" placeholder="auto"></label>
  <label>events<input id="nev" value="__NEVENTS__" style="width:60px"></label>
  <label>bandwidth [MHz]<input id="bw" value="__BW__" placeholder="off" style="width:70px"></label>
  <label>baseline from [ns]<input id="bfrom" value="__BFROM__" placeholder="default" style="width:70px"></label>
  <label>to [ns]<input id="bto" value="__BTO__" placeholder="default" style="width:70px"></label>
  <label>charge gate from [ns]<input id="gfrom" value="__GFROM__" placeholder="default" style="width:70px"></label>
  <label>to [ns]<input id="gto" value="__GTO__" placeholder="default" style="width:70px"></label>
  <label>channels<input id="canali" value="" placeholder="all  e.g. 8,9,12-15" style="width:150px"></label>
  <label>threshold [offset]<input id="qcut" value="" placeholder="whole spectrum" style="width:110px"></label>
  <label>plot size<select id="scala" style="font:13px inherit;padding:5px 7px">
    <option>S</option><option selected>M</option><option>L</option></select></label>
  <label class="chk" title="gate, baseline window, calibration, noise floor and the
set-up warnings. Off during a run, on when you need to know with which numbers a plot was made.">
    <input type="checkbox" id="det"> details</label>
  <button id="reset">Autoscale</button>
  <button id="azzera" title="discards the events already acquired and starts from now">Reset data</button>
  <span class="hint">x/y limits are the waveform zoom only &middot;
    baseline and charge gate are independent of it</span>
</div>
<div id="hctl"></div>
<table id="tab"><thead><tr><th>channel</th><th>baseline</th><th>rms</th>
<th>mean amplitude</th><th>median</th><th>median [mV]</th><th>max</th><th>offset</th><th>threshold</th>
<th>min amp. [mV]</th><th>min amp. [ADC]</th></tr></thead><tbody></tbody></table>
<div id="boot" class="err">JavaScript did not run: the page cannot update.
Open the browser console to see the error.</div>
<!-- Ogni grafico ha il suo "apri" che lo stacca in una finestra a parte, utile
     col secondo schermo: le forme d'onda di la', i controlli di qua. I due
     spettri stanno affiancati, sono due distribuzioni della stessa cosa. -->
<div class="gr"><a class="apri" data-img="pano">&#8599;</a><img id="pano" alt="per-channel overview"></div>
<!-- I tre muri: una miniatura per canale, e sopra una griglia di zone
     trasparenti che aprono la finestra del canale. Le posizioni arrivano da
     stats.json, calcolate dallo stesso codice che disegna. -->
<div class="muro"><img id="mw" alt="waveform wall"><div class="celle" data-muro="wf"></div></div>
<div class="muro"><img id="ma" alt="amplitude wall"><div class="celle" data-muro="amp"></div></div>
<div class="muro"><img id="mc" alt="charge wall"><div class="celle" data-muro="car"></div></div>

<div class="gr"><a class="apri" data-img="a">&#8599;</a><img id="a" alt="average"></div>

<details id="interi"><summary>full-size plots of every channel</summary>
  <div class="gr"><a class="apri" data-img="w">&#8599;</a><img id="w" alt="waveforms"></div>
  <div class="affianca">
    <div class="gr"><a class="apri" data-img="h">&#8599;</a><img id="h" alt="amplitudes"></div>
    <div class="gr"><a class="apri" data-img="q">&#8599;</a><img id="q" alt="charge integrals"></div>
  </div>
</details>
<script>
document.getElementById('boot').style.display = 'none';
const REFRESH = __REFRESH__ * 1000;

let lastOk = null;            // ultimo aggiornamento riuscito

function setAlert(msg) {
  const a = document.getElementById('alert');
  a.style.display = msg ? 'block' : 'none';
  if (msg) a.textContent = msg;
  document.body.classList.toggle('stale', !!msg);
}
const FIELDS = ['xmin','xmax','ymin','ymax','nev','bw','bfrom','bto','gfrom','gto',
                'canali','qcut','scala'];

// I limiti scelti sopravvivono a un reload della pagina. localStorage puo'
// essere inaccessibile (finestra privata, cookie bloccati): mai fatale.
try {
  for (const f of FIELDS) {
    const v = localStorage.getItem('daqmon.' + f);
    if (v !== null) document.getElementById(f).value = v;
  }
} catch (e) {}

// I controlli dell'istogramma sono uno per canale e la lista dei canali si
// conosce solo da stats.json, quindi vengono costruiti al primo giro e
// ricostruiti solo se i canali cambiano: rifarli a ogni aggiornamento
// cancellerebbe quello che si sta digitando.
let builtChannels = null;

function store(k, v) { try { localStorage.setItem('daqmon.' + k, v); } catch (e) {} }
function recall(k)   { try { return localStorage.getItem('daqmon.' + k); } catch (e) { return null; } }

function buildHistControls(channels) {
  const key = channels.join(',');
  if (key === builtChannels) return;
  builtChannels = key;

  document.getElementById('hctl').innerHTML = channels.map(ch => `
    <div class="ctl">
      <span class="grp">ch${ch}</span>
      <label>amplitude x min [offset]<input id="hxmin_${ch}" placeholder="auto"></label>
      <label>amplitude x max [offset]<input id="hxmax_${ch}" placeholder="auto"></label>
      <label>bins<input id="hbin_${ch}" placeholder="auto" style="width:60px"></label>
      <label class="chk"><input type="checkbox" id="hlog_${ch}"> log y</label>
      <button class="hreset" data-ch="${ch}">Autoscale</button>
    </div>
    <div class="ctl">
      <span class="grp">ch${ch}</span>
      <label>charge x min [pC]<input id="qxmin_${ch}" placeholder="auto"></label>
      <label>charge x max [pC]<input id="qxmax_${ch}" placeholder="auto"></label>
      <label>bins<input id="qbin_${ch}" placeholder="auto" style="width:60px"></label>
      <label class="chk"><input type="checkbox" id="qlog_${ch}"> log y</label>
      <button class="qreset" data-ch="${ch}">Autoscale</button>
    </div>`).join('');

  for (const ch of channels) {
    for (const k of ['hxmin_' + ch, 'hxmax_' + ch, 'hbin_' + ch,
                     'qxmin_' + ch, 'qxmax_' + ch, 'qbin_' + ch]) {
      const v = recall(k);
      if (v !== null) document.getElementById(k).value = v;
      document.getElementById(k).addEventListener('change', tick);
    }
    for (const k of ['hlog_' + ch, 'qlog_' + ch]) {
      const lg = document.getElementById(k);
      if (recall(k) !== null) lg.checked = (recall(k) === '1');
      lg.addEventListener('change', tick);
    }
  }

  // Due pulsanti Autoscale distinti, uno per riga: azzerare la scala delle
  // ampiezze mentre si sta guardando la carica, o viceversa, farebbe perdere
  // una vista buona mentre se ne sistema un'altra.
  const azzeraRiga = (pre, b) => {
    const ch = b.dataset.ch;
    document.getElementById(pre + 'xmin_' + ch).value = '';
    document.getElementById(pre + 'xmax_' + ch).value = '';
    document.getElementById(pre + 'bin_' + ch).value = '';
    document.getElementById(pre + 'log_' + ch).checked = false;
    tick();
  };
  document.querySelectorAll('#hctl button.hreset')
          .forEach(b => b.onclick = () => azzeraRiga('h', b));
  document.querySelectorAll('#hctl button.qreset')
          .forEach(b => b.onclick = () => azzeraRiga('q', b));
}

// Finestre separate, una per grafico. Non hanno uno script loro: e' questa
// pagina che, a ogni giro, gli riscrive la src dell'immagine. Cosi' non serve
// una rotta nuova sul server, e una finestra rimasta aperta mentre la pagina
// madre e' chiusa smette semplicemente di aggiornarsi invece di mostrare dati
// vecchi fingendo di essere viva.
const finestre = [];

// Una finestra per canale. Quella delle forme d'onda ne porta DUE: l'ultimo
// evento e la media dello stesso canale, affiancati -- sono due domande
// diverse e si leggono bene vicine.
function apriCanale(muro, ch) {
  const sorgenti = {
    wf:  [["waveforms", "last event"], ["average", "average"]],
    amp: [["amplitudes", "amplitude spectrum"]],
    car: [["integrals", "charge spectrum"]],
  }[muro];
  const nome = "ch" + ch + " \u00b7 " + (muro === "wf" ? "waveform" :
               muro === "amp" ? "amplitude" : "charge");
  const w = window.open("", "daqmon_" + muro + "_" + ch,
                        "width=" + (sorgenti.length > 1 ? 1040 : 560) + ",height=420,scrollbars=yes");
  if (!w) { setAlert("The browser blocked the pop-up window. Allow pop-ups for this page."); return; }
  w.document.open();
  w.document.write(
    "<!DOCTYPE html><html><head><meta charset=utf-8><title>" + nome + "</title>" +
    "<style>html,body{margin:0;background:#16181d;color:#999;" +
    "font:12px system-ui,sans-serif}" +
    "div{display:flex;gap:6px;align-items:flex-start}" +
    "img{max-width:100%;display:block}p{margin:6px 10px}</style></head><body><div>" +
    sorgenti.map(() => "<img>").join("") +
    "</div><p>" + nome + " &middot; aggiornata dalla pagina principale</p></body></html>");
  w.document.close();
  finestre.push({w: w, muro: muro, ch: ch, sorgenti: sorgenti, dim: false});
  aggiornaFinestre();
}

function apriFinestra(id, titolo) {
  const img = document.getElementById(id);
  const w = window.open("", "daqmon_" + id, "width=1020,height=620,scrollbars=yes");
  if (!w) { setAlert("The browser blocked the pop-up window. Allow pop-ups for this page."); return; }
  w.document.open();
  w.document.write(
    "<!DOCTYPE html><html><head><meta charset=utf-8><title>" + titolo + "</title>" +
    "<style>html,body{margin:0;background:#16181d;color:#999;" +
    "font:12px system-ui,sans-serif}" +
    "img{max-width:100%;display:block}" +
    "p{margin:6px 10px}</style></head><body>" +
    "<img src='" + img.src + "'>" +
    "<p>" + titolo + " &middot; aggiornato dalla pagina principale: se la chiudi, questa si ferma.</p>" +
    "</body></html>");
  w.document.close();
  finestre.push({w: w, id: id, dim: false});
}

function aggiornaFinestre() {
  const p = params();
  for (let i = finestre.length - 1; i >= 0; i--) {
    const f = finestre[i];
    if (f.w.closed) { finestre.splice(i, 1); continue; }
    try {
      // images[] e non getElementById: gli elementi stanno nella finestra
      // FIGLIA, e un getElementById qui dentro fa credere a check_page.py che
      // questa pagina abbia un id che non ha.
      const dentro = f.w.document.images;
      if (f.sorgenti) {
        // finestra di un canale: si chiedono le immagini con "solo", che
        // disegna quel canale senza toccare i canali LETTI, che sono
        // condivisi con chi guarda la stessa pagina da un'altra macchina
        const q = new URLSearchParams(p);
        q.set("solo", f.ch);
        q.set("t", Date.now());
        f.sorgenti.forEach((sg, k) => {
          if (dentro[k]) dentro[k].src = sg[0] + ".png?" + q.toString();
        });
        // La finestra si ridimensiona sulla misura VERA delle immagini, una
        // volta sola. Le width/height passate a window.open sono un
        // suggerimento che Safari ignora: si apriva grande quanto lo schermo
        // con dentro un grafico da 330 px in un angolo.
        if (!f.dim && dentro.length &&
            Array.prototype.every.call(dentro, im => im.naturalWidth > 0)) {
          let lw = 0, lh = 0;
          Array.prototype.forEach.call(dentro, im => {
            lw += im.naturalWidth + 8;
            lh = Math.max(lh, im.naturalHeight);
          });
          const bx = Math.max(0, f.w.outerWidth - f.w.innerWidth);
          const by = Math.max(0, f.w.outerHeight - f.w.innerHeight);
          try { f.w.resizeTo(lw + bx + 16, lh + by + 54); } catch (e) {}
          f.dim = true;
        }
      } else if (dentro[0]) {
        dentro[0].src = document.getElementById(f.id).src;
        if (!f.dim && dentro[0].naturalWidth > 0) {
          const bx = Math.max(0, f.w.outerWidth - f.w.innerWidth);
          const by = Math.max(0, f.w.outerHeight - f.w.innerHeight);
          try {
            f.w.resizeTo(dentro[0].naturalWidth + bx + 16,
                         dentro[0].naturalHeight + by + 54);
          } catch (e) {}
          f.dim = true;
        }
      }
    } catch (e) { finestre.splice(i, 1); }
  }
}

// Le zone cliccabili sopra i muri: posizioni e canali arrivano da stats.json,
// calcolati dallo stesso codice che disegna le celle.
function costruisciCelle(muro) {
  if (!muro || !muro.celle) return;
  const firma = muro.celle.map(c => c.ch).join(",") + "|" + muro.ncol;
  document.querySelectorAll(".celle").forEach(box => {
    if (box.dataset.firma === firma) return;
    box.dataset.firma = firma;
    box.innerHTML = muro.celle.map(c =>
      `<a title="ch${c.ch}" data-ch="${c.ch}" style="left:${c.x * 100}%;` +
      `bottom:${c.y * 100}%;width:${c.w * 100}%;height:${c.h * 100}%"></a>`).join("");
    box.querySelectorAll("a").forEach(a => {
      a.onclick = () => apriCanale(box.dataset.muro, a.dataset.ch);
    });
  });
}

function params() {
  const p = new URLSearchParams();
  // Fuori da FIELDS perche' e' una casella di spunta: FIELDS legge .value, e
  // su una checkbox .value e' sempre "on".
  const det = document.getElementById('det').checked;
  try { localStorage.setItem('daqmon.det', det ? '1' : '0'); } catch (e) {}
  p.set('det', det ? '1' : '0');
  for (const f of FIELDS) {
    const v = document.getElementById(f).value.trim();
    try { localStorage.setItem('daqmon.' + f, v); } catch (e) {}
    if (v !== '') p.set(f === 'nev' ? 'n' : f, v);
  }
  if (builtChannels) for (const ch of builtChannels.split(',')) {
    for (const k of ['hxmin_' + ch, 'hxmax_' + ch, 'hbin_' + ch,
                     'qxmin_' + ch, 'qxmax_' + ch, 'qbin_' + ch]) {
      const v = document.getElementById(k).value.trim();
      store(k, v);
      if (v !== '') p.set(k, v);
    }
    for (const k of ['hlog_' + ch, 'qlog_' + ch]) {
      const on = document.getElementById(k).checked;
      store(k, on ? '1' : '0');
      p.set(k, on ? '1' : '0');
    }
  }
  return p;
}

document.querySelectorAll('.gr .apri').forEach(a => {
  a.title = 'open in a separate window';
  a.onclick = () => apriFinestra(a.dataset.img,
                                 document.getElementById(a.dataset.img).alt);
});

document.getElementById('azzera').onclick = async () => {
  // Non ricarica la pagina: l'azzeramento vive nel server, e il giro
  // successivo ritrova gli istogrammi vuoti da soli.
  try { await fetch('azzera?t=' + Date.now()); } catch (e) {}
  tick();
};

document.getElementById('reset').onclick = () => {
  for (const f of ['xmin','xmax','ymin','ymax']) document.getElementById(f).value = '';
  tick();
};
for (const f of FIELDS)
  document.getElementById(f).addEventListener('change', tick);
function show(id, v) {
  document.getElementById(id).textContent = (v === null || v === undefined) ? '-' : v;
}
async function tick() {
  try {
    const r = await fetch('stats.json?det=' +
                          (document.getElementById('det').checked ? '1' : '0'),
                          {cache:'no-store'});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const s = await r.json();
    lastOk = new Date();

    // Un file che non cresce non e' una run in corso. Quando una run finisce
    // il suo file viene compresso e questo monitor ripiega sul .h5 non
    // compresso piu' recente rimasto in data/, che puo' essere di giorni
    // prima: stessi grafici, stessi numeri, nessun avviso. E' successo, con
    // un file del 1 ottobre mostrato come se fosse la run del momento.
    const FERMA_S = 15;
    const ferma = s.ferma_da !== null && s.ferma_da !== undefined
                  && s.ferma_da > FERMA_S;
    if (ferma) {
      const quando = s.file_quando
        ? new Date(s.file_quando * 1000).toLocaleString() : 'unknown date';
      setAlert('No run in progress. Showing ' + (s.file || '?') +
               ', last written ' + quando + ' (' + Math.round(s.ferma_da) +
               ' s ago). These are not live data.');
    } else {
      setAlert(null);
    }
    document.getElementById('upd').textContent = ferma
      ? 'file not growing since ' + Math.round(s.ferma_da) + ' s'
      : 'updated at ' + lastOk.toLocaleTimeString();
    show('rate100', s.rate100); show('ratemed', s.ratemed);
    document.getElementById('ratemednota').textContent =
        'Hz · run average' + (s.ratemednota || '');
    show('events', s.events); show('shown', s.shown);
    document.getElementById('err').textContent    = s.error || '';
    document.getElementById('file').textContent   =
      (s.file || 'no file') + (s.sampling ? ' · ' + s.sampling : '');
    buildHistControls((s.channels || []).map(c => c.ch));
    const tb = document.querySelector('#tab tbody');
    const na = v => (v === null || v === undefined) ? '-' : v;
    tb.innerHTML = (s.channels||[]).map(c =>
      `<tr><td>ch${c.ch}</td><td>${c.baseline}</td><td>${c.rms}</td>
       <td>${c.amp_mean}</td><td><b>${na(c.amp_med)}</b></td>
       <td><b>${na(c.amp_med_mv)}</b></td><td>${c.amp_max}</td>
       <td>${na(c.offset)}</td><td>${na(c.threshold)}</td>
       <td>${c.eff_note ? '<span class="err">'+c.eff_note+'</span>' : na(c.eff_mv)}</td>
       <td>${na(c.eff)}</td></tr>`).join('');
    const p = params();
    p.set('t', Date.now());
    for (const [id, name] of [['w','waveforms'],['a','average'],['h','amplitudes'],
                             ['q','integrals'],
                             ['mw','muro_wf'],['ma','muro_amp'],['mc','muro_car']])
      document.getElementById(id).src = name + '.png?' + p.toString();
    costruisciCelle(s.muro);
    // La panoramica copre tutti i canali e non risente della selezione, quindi
    // non serve rigenerarla a ogni giro: si aggiorna ogni 5 s per conto suo.
    const pano = document.getElementById('pano');
    if (!pano.dataset.t || (Date.now() - pano.dataset.t) > 5000) {
      pano.dataset.t = Date.now();
      pano.src = 'panoramica.png?' + p.toString();
    }
    aggiornaFinestre();
  } catch (e) {
    // Le immagini restano quelle di prima: senza un avviso vistoso la pagina
    // sembrerebbe viva mentre mostra dati fermi.
    const why = (e && e.message) ? e.message : String(e);
    const msg = lastOk
      ? `Server unreachable (${why}). Data frozen at the last successful `
        + `update: ${lastOk.toLocaleTimeString()}, `
        + `${Math.round((Date.now() - lastOk.getTime()) / 1000)} s ago. `
        + `The monitor on the DAQ machine has probably been shut down.`
      : `Server unreachable (${why}). No data received since the page was `
        + `opened: check that the monitor is running.`;
    setAlert(msg);
    document.getElementById('err').textContent = '';
  }
}
try {
  if (localStorage.getItem('daqmon.det') === '1')
    document.getElementById('det').checked = true;
} catch (e) {}
document.getElementById('det').addEventListener('change', tick);

tick(); setInterval(tick, REFRESH);
</script></body></html>"""


# Canali della board. Il V1742 ne ha 32, quattro gruppi da otto: l'asse della
# panoramica li porta tutti, sempre, perche' il posto di un canale non deve
# dipendere da quanti se ne registrano quel giorno.
N_CANALI_HW = 32

# Larghezza comune a tutte le figure, in PIXEL e non in pollici: la panoramica
# si salva a 110 dpi e le altre a 100, quindi gli stessi pollici darebbero
# larghezze diverse sullo schermo. E' il numero che tiene in colonna la
# panoramica, le forme d'onda e la media, con i due spettri che si dividono la
# stessa larghezza in due.
# Due colori per due grandezze diverse: prima erano tutti e due blu e i due
# spettri si somigliavano al punto da scambiarli. Sono i primi due della
# tavolozza di plot_scan.py, scelta perche' le coppie restano distinguibili
# anche con il daltonismo -- blu e arancio e' la coppia sicura, verde e
# arancio no.
# Ordine fisso, mai ciclato: oltre l'ottavo canale le curve non si
# distinguono piu', ed e' la stessa regola che plot_scan.py applica da
# settembre. E' la tavolozza di quel file.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
           "#e87ba4", "#008300", "#4a3aa7", "#e34948"]

COLORE_AMPIEZZA = "#2a78d6"     # spettro delle ampiezze, in unita' di offset
COLORE_CARICA = "#eb6834"       # spettro di carica, in pC

LARG_PX = 660
DPI_PAN = 110
DPI_FIG = 100


# --- geometria dei muri ------------------------------------------------
# Le celle si posizionano A MANO, non con subplots: la pagina deve sapere
# ESATTAMENTE dove cade ogni canale per mettere sopra l'immagine le zone
# cliccabili. Con tight_layout le posizioni le decide matplotlib e dalla
# pagina si potrebbero solo indovinare.
MURO_COL = 4            # canali per riga
MURO_MX, MURO_MY = 0.012, 0.030      # margini laterali e in basso
MURO_TOP = 0.90         # sotto il titolo
MURO_GX, MURO_GY = 0.012, 0.055      # spazio fra le celle


def geometria_muro(n):
    """(ncol, nrig, [(x, y, w, h) per canale]) in frazioni della figura."""
    ncol = min(MURO_COL, max(1, n))
    nrig = int(math.ceil(n / ncol)) if n else 1
    w = (MURO_TOP - MURO_MX * 0 + 0)  # segnaposto, calcolato sotto
    w = ((1.0 - 2 * MURO_MX) - (ncol - 1) * MURO_GX) / ncol
    h = ((MURO_TOP - MURO_MY) - (nrig - 1) * MURO_GY) / nrig
    celle = []
    for i in range(n):
        r, c = divmod(i, ncol)
        x = MURO_MX + c * (w + MURO_GX)
        y = MURO_TOP - (r + 1) * h - r * MURO_GY
        celle.append((round(x, 5), round(y, 5), round(w, 5), round(h, 5)))
    return ncol, nrig, celle


def _bin(valore):
    """Numero di bin chiesto dalla pagina, oppure None per automatico.

    Si limita l'intervallo: un numero enorme non produce un istogramma piu'
    informativo -- con mille eventi e mille bin si guarda il rumore di Poisson
    -- ma fa disegnare a matplotlib migliaia di rettangoli a ogni
    aggiornamento, su un monitor che si ridisegna ogni secondo.
    """
    if valore is None:
        return None
    n = int(valore)
    return max(5, min(500, n))


def parse_channels(testo):
    """Interpreta "8,9,12-15" come [8, 9, 12, 13, 14, 15]. Vuoto = tutti.

    Un intervallo scritto al contrario o un pezzo non numerico vengono
    ignorati: e' una casella di testo in una pagina, e non deve poter far
    cadere il server.
    """
    testo = (testo or "").strip()
    if not testo:
        return None
    fuori = []
    for pezzo in testo.replace(";", ",").split(","):
        pezzo = pezzo.strip()
        if not pezzo:
            continue
        if "-" in pezzo:
            a, _, b = pezzo.partition("-")
            try:
                a, b = int(a), int(b)
            except ValueError:
                continue
            if a <= b:
                fuori.extend(range(a, b + 1))
        else:
            try:
                fuori.append(int(pezzo))
            except ValueError:
                continue
    # senza duplicati e in ordine, cosi' i grafici non dipendono da come si
    # e' scritto l'elenco
    return sorted(set(fuori)) or None


def _fmt(v):
    """Valore per un campo della pagina: vuoto se non impostato."""
    return "" if v is None else str(v)


def make_handler(monitor, refresh, defaults):

    class Handler(BaseHTTPRequestHandler):

        def log_message(self, *a):         # niente log di accesso sul terminale
            pass

        @staticmethod
        def _num(qs, key, default=None):
            """Un parametro numerico dalla query string; vuoto o invalido = default."""
            raw = qs.get(key, [""])[0].strip()
            if raw == "":
                return default
            try:
                return float(raw)
            except ValueError:
                return default

        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            route = parsed.path.lstrip("/") or "index.html"
            qs = parse_qs(parsed.query)

            if route == "favicon.ico":
                # Il browser la chiede da solo: senza questa riga la console
                # si riempie di 404 e nasconde gli errori veri.
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            if route == "index.html":
                page = (PAGE.replace("__REFRESH__", str(refresh))
                            .replace("__NEVENTS__", str(defaults["n"]))
                            .replace("__BW__", _fmt(defaults["bw"]))
                            .replace("__XMIN__", _fmt(defaults["xmin"]))
                            .replace("__XMAX__", _fmt(defaults["xmax"]))
                            .replace("__YMIN__", _fmt(defaults["ymin"]))
                            .replace("__YMAX__", _fmt(defaults["ymax"]))
                            .replace("__BFROM__", _fmt(defaults["bfrom"]))
                            .replace("__BTO__", _fmt(defaults["bto"]))
                            .replace("__GFROM__", _fmt(defaults["gfrom"]))
                            .replace("__GTO__", _fmt(defaults["gto"]))
)
                return self._send(200, "text/html; charset=utf-8", page.encode())

            # La finestra del piedistallo e' stato del monitor, non un
            # parametro di disegno: la usano l'analisi, le statistiche e le
            # cariche accumulate, non solo la figura che si sta chiedendo.
            monitor.set_baseline_ns(self._num(qs, "bfrom", defaults["bfrom"]),
                                    self._num(qs, "bto", defaults["bto"]))

            # I valori della pagina hanno la precedenza su quelli da riga di comando
            xlim = (self._num(qs, "xmin", defaults["xmin"]),
                    self._num(qs, "xmax", defaults["xmax"]))
            ylim = (self._num(qs, "ymin", defaults["ymin"]),
                    self._num(qs, "ymax", defaults["ymax"]))
            n = int(self._num(qs, "n", defaults["n"]) or defaults["n"])



            with monitor.lock:
                monitor.select_channels(parse_channels(qs.get("canali", [""])[0]))
                monitor.refresh()

                if route == "azzera":
                    monitor.azzera()
                    return self._send(200, "application/json",
                                      json.dumps({"ok": True,
                                                  "da_evento": monitor.origin}).encode())

                if route == "stats.json":
                    return self._send(200, "application/json",
                                      json.dumps(monitor.stats(
                                          qs.get("det", ["0"])[0] == "1")).encode())

                kinds = {"panoramica.png": "panoramica",
                         "waveforms.png": "waveforms",
                         "average.png": "average",
                         "amplitudes.png": "amplitudes",
                         "integrals.png": "integrals",
                         "muro_wf.png": "muro_wf",
                         "muro_amp.png": "muro_amp",
                         "muro_car.png": "muro_car"}
                if route in kinds:
                    # I limiti dell'istogramma sono per canale: hxmin_8, hlog_9, ...
                    # I valori da riga di comando fanno da default per tutti.
                    hset, qset = {}, {}
                    for ch in (monitor.hdr or {}).get("ChannelList", []):
                        ch = int(ch)
                        hset[ch] = (
                            self._num(qs, f"hxmin_{ch}", defaults["hxmin"]),
                            self._num(qs, f"hxmax_{ch}", defaults["hxmax"]),
                            qs.get(f"hlog_{ch}",
                                   ["1" if defaults["hlog"] else "0"])[0] == "1",
                            _bin(self._num(qs, f"hbin_{ch}", None)),
                        )
                        # Lo spettro di carica ha la sua scala: e' in pC, e con
                        # i limiti delle ampiezze -- che sono in unita' di
                        # offset -- si sarebbe ritagliato su numeri che li' non
                        # vogliono dire niente.
                        qset[ch] = (
                            self._num(qs, f"qxmin_{ch}", None),
                            self._num(qs, f"qxmax_{ch}", None),
                            qs.get(f"qlog_{ch}", ["0"])[0] == "1",
                            _bin(self._num(qs, f"qbin_{ch}", None)),
                        )
                    gate = (self._num(qs, "gfrom", defaults["gfrom"]),
                            self._num(qs, "gto", defaults["gto"]))
                    return self._send(200, "image/png",
                                      monitor.figure(kinds[route], n, xlim, ylim,
                                                     hset,
                                                     self._num(qs, "bw", defaults["bw"]),
                                                     self._num(qs, "qcut", defaults["qcut"]),
                                                     qset, gate,
                                                     qs.get("scala", ["M"])[0],
                                                     qs.get("det", ["0"])[0] == "1",
                                                     self._num(qs, "solo", None)))

            self._send(404, "text/plain", b"not found")

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", help="file da monitorare (default: il piu' recente)")
    ap.add_argument("-d", "--data-dir", default=None,
                    help="directory dei dati (default: <radice del progetto>/data)")
    ap.add_argument("-p", "--port", type=int, default=8765)
    ap.add_argument("--qcut", type=float, default=None,
                    help="soglia in unita' di offset: sopra gli istogrammi compare la "
                         "frazione di eventi che la supera. Vuoto = estremo inferiore "
                         "dello spettro, cioe' conta tutti gli eventi")
    ap.add_argument("--attenuazione", type=float, default=None,
                    help="attenuazione del Transparent Mode usata per convertire "
                         "l'ampiezza in unita' di offset. Vuoto = quella misurata "
                         "per la frequenza in uso, valida per impulsi da ~1.8 ns")
    ap.add_argument("-b", "--bind", default="127.0.0.1",
                    help="indirizzo su cui ascoltare. Il default accetta solo "
                         "connessioni locali, quindi da fuori serve un inoltro "
                         "di porta. Con 0.0.0.0 il monitor e' raggiungibile "
                         "direttamente dalla rete e non serve nessun tunnel, "
                         "che e' un pezzo in meno che si puo' rompere")
    ap.add_argument("-n", "--nevents", type=int, default=1,
                    help="eventi sovrapposti nel grafico (default 1 = solo l'ultimo)")
    ap.add_argument("--tail-cut", type=int, default=Monitor.TAGLIO_CODA,
                    help="campioni finali da scartare: il V1742 ci mette spesso "
                         "un picco positivo spurio (0 per non scartarne)")
    ap.add_argument("--baseline-from", type=float, default=10.0, dest="bfrom",
                    help="inizio della finestra in cui si misura il piedistallo "
                         "[ns] (default 10: i primi campioni dopo la cella di "
                         "trigger del DRS4 non sono puliti)")
    ap.add_argument("--baseline-to", type=float, default=180.0, dest="bto",
                    help="fine della finestra del piedistallo [ns] (default 180). "
                         "Deve stare PRIMA dell'impulso: a frequenze di "
                         "campionamento alte la finestra intera e' corta e questi "
                         "valori vanno rivisti. Con fine <= inizio non si corregge "
                         "niente e si torna alla mediana dell'intera traccia")
    ap.add_argument("--gate-from", type=float, default=190.0, dest="gfrom",
                    help="inizio del cancello su cui si integra la carica [ns] "
                         "(default 190). Indipendente dallo zoom: la forma d'onda "
                         "si guarda intera, la carica si integra dove c'e' l'impulso")
    ap.add_argument("--gate-to", type=float, default=1000.0, dest="gto",
                    help="fine del cancello della carica [ns] (default 1000)")
    ap.add_argument("--bw", type=float, default=None,
                    help="mostra il segnale dopo un passa-basso a questa frequenza "
                         "[MHz], piu' le letture di un ADC a 30 MHz. Serve a vedere "
                         "cosa arriva al comparatore del self-trigger")
    ap.add_argument("--xmin", type=float, default=None, help="limite inferiore asse tempi [ns]")
    ap.add_argument("--xmax", type=float, default=None, help="limite superiore asse tempi [ns]")
    ap.add_argument("--ymin", type=float, default=None, help="limite inferiore asse ampiezze [ADC]")
    ap.add_argument("--ymax", type=float, default=None, help="limite superiore asse ampiezze [ADC]")
    ap.add_argument("--hxmin", type=float, default=None,
                    help="limite inferiore dell'istogramma delle ampiezze [ADC]")
    ap.add_argument("--hxmax", type=float, default=None,
                    help="limite superiore dell'istogramma delle ampiezze [ADC]")
    ap.add_argument("--hlog", action="store_true",
                    help="asse y logaritmico nell'istogramma delle ampiezze")
    ap.add_argument("-m", "--max-events", type=int, default=2000,
                    help="eventi piu' recenti usati per le statistiche (default 2000; "
                         "sotto il migliaio la stima della soglia non e' stabile)")
    ap.add_argument("--vpp", type=float, default=1.0,
                    help="range di ingresso del digitizer in Vpp (default 1.0; "
                         "2.0 per la versione VPERS1742)")
    ap.add_argument("-r", "--refresh", type=float, default=2,
                    help="secondi fra un aggiornamento e l'altro (default 5)")
    args = ap.parse_args()

    # Il default va ancorato alla radice del progetto, non alla directory
    # corrente: lanciando lo script da tools/ un "data" relativo punterebbe a
    # tools/data, che non esiste, e il monitor resterebbe vuoto senza spiegare
    # perche'.
    data_dir = args.data_dir
    if data_dir is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(root, "data")

    if not os.path.isdir(data_dir):
        sys.exit(f"La directory dei dati non esiste: {os.path.abspath(data_dir)}\n"
                 "Indicane un'altra con -d.")

    print(f"Dati letti da: {os.path.abspath(data_dir)}")

    monitor = Monitor(data_dir, args.file, args.max_events,
                      min_interval=max(0.3, args.refresh / 2), vpp=args.vpp)
    monitor.tail_cut = max(0, args.tail_cut)
    monitor.att_forzata = args.attenuazione
    monitor.baseline_ns = (args.bfrom, args.bto)
    defaults = {"n": args.nevents, "bw": args.bw, "qcut": args.qcut,
                "xmin": args.xmin, "xmax": args.xmax,
                "ymin": args.ymin, "ymax": args.ymax,
                "hxmin": args.hxmin, "hxmax": args.hxmax, "hlog": args.hlog,
                "bfrom": args.bfrom, "bto": args.bto,
                "gfrom": args.gfrom, "gto": args.gto}

    try:
        server = ThreadingHTTPServer((args.bind, args.port),
                                     make_handler(monitor, args.refresh, defaults))
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        # Caso tipico: un'altra istanza del monitor e' gia' attiva, magari
        # avviata da qualcun altro o lasciata in background.
        sys.exit(
            f"La porta {args.port} e' gia' in uso.\n\n"
            "Di solito significa che un monitor e' gia' attivo: prova ad aprire\n"
            f"  http://localhost:{args.port}\n"
            "prima di lanciarne un altro.\n\n"
            "Per vedere chi la occupa:   ss -ltnp | grep " + str(args.port) + "\n"
            f"Per usare un'altra porta:   {os.path.basename(sys.argv[0])} -p {args.port + 1}"
        )

    if args.bind in ("127.0.0.1", "localhost"):
        print(f"Monitor attivo su http://localhost:{args.port}")
        print("Ascolta solo in locale: da fuori serve un inoltro di porta.")
        print("Per raggiungerlo direttamente dalla rete, senza tunnel:")
        print(f"    python3 {os.path.basename(sys.argv[0])} -b 0.0.0.0 -p {args.port}")
    else:
        import socket as _s
        try:
            ip = _s.gethostbyname(_s.gethostname())
        except OSError:
            ip = args.bind
        print(f"Monitor attivo su http://{ip}:{args.port}")
        print("Ascolta su tutta la rete: niente inoltri di porta da mantenere.")
    print("Ctrl+C per fermare.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nMonitor fermato.")


if __name__ == "__main__":
    main()
