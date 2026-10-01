#!/usr/bin/env python3
"""Controllo della DAQ da browser: stato, avvio e arresto.

    python3 tools/daq_control.py                 # solo da questa macchina
    python3 tools/daq_control.py -b 0.0.0.0      # raggiungibile dalla rete

Staccato dal terminale, cosi' sopravvive alla caduta della rete:

    setsid nohup python3 /home/daq/daq-standalone/tools/daq_control.py \
        -b 0.0.0.0 < /dev/null > /tmp/daq_control.out 2>&1 &

Il percorso ASSOLUTO non e' pignoleria: lanciandolo con un percorso relativo
da una directory qualsiasi -- tipo build/, dove si finisce dopo aver avviato
la DAQ a mano -- python non trova il file e il servizio muore all'istante,
lasciando un browser che non si collega e nessun indizio evidente.

Sta deliberatamente SEPARATO da live_monitor.py. Il monitor e' in sola
lettura, lo puoi riavviare quando vuoi e un errore nel disegnare un grafico
non puo' fare danni; questo processo invece possiede il ciclo di vita della
run. Tenerli distinti vuol dire poter riavviare il monitor mentre una run va
avanti, e viceversa.

Due principi, da non perdere strada facendo:

  - Il TOML resta l'unica fonte di verita'. Qui dentro non c'e' nessuno stato
    nascosto: tutto quello che la pagina mostra si ricava dal filesystem e
    dalla tabella dei processi, quindi riavviare questo servizio non perde
    niente e non puo' divergere dalla realta'.

  - L'arresto e' un segnale, non un'uccisione. La DAQ gestisce SIGTERM e
    chiude la run per la porta buona: file compresso e board resettata. Un
    pulsante Stop che facesse kill -9 rimetterebbe in piedi tutti i problemi
    che quel meccanismo e' servito a togliere.
"""

import argparse
import errno
import io
import re
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tomledit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BINARIO = os.path.join(ROOT, "build", "main", "DAQ-WC")
AZIONI = os.path.join(ROOT, "data", "azioni.jsonl")

# ---------------------------------------------------------------------------
#  Quali chiavi si possono cambiare dalla pagina
#
#  Deliberatamente poche: quelle che si cambiano davvero fra una run e
#  l'altra. Tutto il resto resta nel TOML, dove ha accanto il commento che
#  spiega perche' vale quello che vale. Esporre ogni chiave vorrebbe dire
#  trasformare la pagina in un editor peggiore di un editor.
#
#  (sezione, chiave, etichetta, tipo, dettagli)
# ---------------------------------------------------------------------------
CAMPI = [
    ("digitizer", "SamplingRate",    "campionamento",      "scelta", ["5GHz", "2.5GHz", "1GHz"]),
    ("digitizer", "RecordLength",    "campioni per evento", "intero", (1, 1024)),
    ("digitizer", "PostTriggerSize", "post-trigger [%]",   "intero", (0, 100)),
    ("digitizer", "NEvents",         "eventi da acquisire", "intero", (1, 10**9)),
    ("digitizer", "TailCut",         "campioni finali scartati", "intero", (0, 200)),
    ("digitizer", "ChannelList",     "canali registrati",  "lista",  (0, 31)),
    ("digitizer", "Connection",      "collegamento",       "scelta", ["auto", "ETH_V4718", "USB_A4818"]),
    ("digitizer", "DRS4Correction",  "correzioni DRS4",    "booleano", None),
    ("digitizer", "OutputFile",      "prefisso dei file",  "testo",  None),

    ("digitizer", "ExternalTrigger", "trigger esterno (TRG-IN)", "booleano", None),
    ("digitizer", "SelfTrigger",     "self-trigger",       "booleano", None),
    ("digitizer", "SelfTriggerMode", "modo del self-trigger", "scelta", ["paired", "global"]),
    ("digitizer", "SelfTriggerChannels", "canali in self-trigger", "lista", (0, 31)),
    ("digitizer", "SelfTriggerThresholdOffset", "offset di soglia", "lista", (0, 4095)),
    ("digitizer", "TriggerOut",      "cosa esce da TRG-OUT", "scelta", ["self", "all", "off", "default"]),

    ("cfd", "Enabled",   "CFD V812 attivo",     "booleano", None),
    ("cfd", "Threshold", "soglie CFD [mV]",     "lista", (5, 255)),
    ("cfd", "Channels",  "ingressi CFD usati",  "lista", (0, 15)),
    ("cfd", "Width",     "larghezza uscita [conteggi]", "intero", (0, 255)),
    ("cfd", "DeadTime",  "tempo morto [conteggi]",      "intero", (0, 255)),
    ("cfd", "Majority",  "maggioranza (1 = OR)", "intero", (1, 20)),

    ("settings", "verbosity", "verbosita' a schermo", "intero", (0, 4)),
]

# Chiavi che valgono SUBITO, senza riavviare la run. Sono le uniche: tutto il
# resto la DAQ lo legge una volta sola all'avvio, e dirlo nella pagina evita
# la domanda "ho cambiato il campionamento e non succede niente".
A_CALDO = {("digitizer", "SelfTriggerThresholdOffset")}

# Chiavi che la pagina mostra come tabella per canale invece che come lista
# da scrivere a mano. Sono due tabelle e non una perche' i canali del V1742
# (0-31) e gli ingressi del V812 (0-15) sono numerazioni DIVERSE: l'ingresso
# 3 del CFD e' quello dove hai infilato il cavo, e quale canale del digitizer
# gli corrisponda dipende dal cablaggio, che il software non puo' sapere.
TABELLA = {
    ("digitizer", "ChannelList"): "dig",
    ("digitizer", "SelfTriggerChannels"): "dig",
    ("digitizer", "SelfTriggerThresholdOffset"): "dig",
    ("cfd", "Channels"): "cfd",
    ("cfd", "Threshold"): "cfd",
}


def _ui_da_toml(tipo, grezzo):
    """Dal testo nel file a quello che si mostra nella casella."""
    g = grezzo.strip()
    if tipo in ("scelta", "testo"):
        return g[1:-1] if len(g) >= 2 and g[0] in "\"'" and g[-1] == g[0] else g
    if tipo == "lista":
        return g[1:-1].strip() if g.startswith("[") else g
    return g


def _toml_da_ui(tipo, valore, dettagli, etichetta):
    """Dalla casella al testo da scrivere nel file. Solleva ValueError."""
    v = (valore or "").strip()
    if tipo == "booleano":
        if v not in ("true", "false"):
            raise ValueError("%s: ammessi solo true e false" % etichetta)
        return v
    if tipo == "scelta":
        if v not in dettagli:
            raise ValueError("%s: ammessi %s" % (etichetta, ", ".join(dettagli)))
        return '"%s"' % v
    if tipo == "testo":
        if '"' in v:
            raise ValueError("%s: niente virgolette dentro il valore" % etichetta)
        return '"%s"' % v
    if tipo == "intero":
        try:
            n = int(v)
        except ValueError:
            raise ValueError("%s: ci vuole un numero intero" % etichetta)
        lo, hi = dettagli
        if not lo <= n <= hi:
            raise ValueError("%s: fuori intervallo, ammessi da %d a %d" % (etichetta, lo, hi))
        return str(n)
    if tipo == "lista":
        pezzi = [p for p in v.replace(",", " ").split() if p]
        if not pezzi:
            raise ValueError("%s: la lista e' vuota" % etichetta)
        numeri = []
        lo, hi = dettagli
        for p in pezzi:
            try:
                n = float(p) if "." in p else int(p)
            except ValueError:
                raise ValueError("%s: '%s' non e' un numero" % (etichetta, p))
            if not lo <= n <= hi:
                raise ValueError("%s: %s fuori intervallo, ammessi da %d a %d"
                                 % (etichetta, p, lo, hi))
            numeri.append(p)
        return "[" + ", ".join(numeri) + "]"
    raise ValueError("tipo sconosciuto: %s" % tipo)


# Latenza del self-trigger, in nanosecondi: e' il tempo fra il superamento
# della soglia e l'arresto del DRS4, e sposta l'impulso dentro la finestra.
LATENZA_NS = {"paired": 320.0, "global": 420.0}
PASSO_NS = {"5GHz": 0.2, "2.5GHz": 0.4, "1GHz": 1.0, "750MHz": 1.333}


def coerenza(d):
    """Controlli sulla configurazione nel suo insieme. Torna (errori, avvisi).

    Un campo per volta puo' essere legittimo e l'insieme no: e' qui che si
    intercettano le combinazioni che fanno perdere una serata. Gli errori
    bloccano la scrittura, gli avvisi no -- alcune combinazioni strane sono
    volute, per esempio triggerare su un canale che non si registra, che e'
    esattamente come si misura l'efficienza di un trigger con un altro.
    """
    errori, avvisi = [], []
    g = d.get("digitizer", {})
    c = d.get("cfd", {})

    def lista(x):
        if x is None:
            return []
        return list(x) if isinstance(x, (list, tuple)) else [x]

    canali = lista(g.get("ChannelList"))
    if not canali:
        errori.append("ChannelList e' vuota: non si registrerebbe niente.")

    self_on = bool(g.get("SelfTrigger"))
    est_on = bool(g.get("ExternalTrigger"))
    modo = str(g.get("SelfTriggerMode", "paired"))
    freq = str(g.get("SamplingRate", ""))

    if not self_on and not est_on and not c.get("Enabled"):
        avvisi.append("Ne self-trigger ne trigger esterno: resterebbe solo il "
                      "trigger software, e la run non acquisirebbe niente da sola.")

    if self_on:
        sch = lista(g.get("SelfTriggerChannels")) or canali
        fuori = [x for x in sch if x not in canali]
        if fuori:
            avvisi.append("Canali in self-trigger ma non registrati: %s. Legittimo "
                          "se vuoi triggerare su uno e guardarne un altro, sbagliato "
                          "se non era quello che volevi." % fuori)
        off = lista(g.get("SelfTriggerThresholdOffset"))
        if len(off) > 1 and len(off) != len(sch):
            avvisi.append("%d soglie per %d canali in self-trigger: la DAQ replica "
                          "l'ultima sui rimanenti." % (len(off), len(sch)))

        if freq == "5GHz":
            errori.append("A 5 GHz il self-trigger non puo' funzionare: la finestra "
                          "dura meno della latenza, l'impulso cade sempre fuori.")
        elif freq == "2.5GHz" and modo == "global":
            avvisi.append("A 2.5 GHz in modo global la latenza (~420 ns) supera la "
                          "finestra (410 ns): l'impulso rischia di restare fuori. "
                          "In paired funziona.")

        # Dove cade l'impulso nella finestra. E' il conto che ci ha gia' fatto
        # registrare eventi vuoti senza capire perche'.
        passo = PASSO_NS.get(freq)
        rl = g.get("RecordLength")
        pt = g.get("PostTriggerSize")
        if passo and isinstance(rl, int) and isinstance(pt, (int, float)):
            finestra = rl * passo
            pos = (1.0 - pt / 100.0) * finestra - LATENZA_NS.get(modo, 320.0)
            if pos < 0:
                errori.append("Con questi valori l'impulso cadrebbe %.0f ns PRIMA "
                              "dell'inizio della finestra: abbassa il post-trigger "
                              "o rallenta il campionamento." % (-pos))
            elif pos > finestra * 0.9:
                avvisi.append("L'impulso cadrebbe a %.0f ns su una finestra di %.0f: "
                              "troppo vicino alla fine, rischi di tagliarne la coda."
                              % (pos, finestra))

    if c.get("Enabled"):
        if str(g.get("Connection", "")) == "USB_A4818":
            errori.append("Col CFD acceso serve il bridge: su USB_A4818 la fibra va "
                          "dritta al digitizer e sul bus VME non c'e' nessun master.")
        if not est_on:
            avvisi.append("CFD acceso ma trigger esterno spento: l'OR del V812 entra "
                          "da TRG-IN, quindi cosi' non fa niente.")
        cch = lista(c.get("Channels"))
        cth = lista(c.get("Threshold"))
        if not cch:
            errori.append("Il CFD e' acceso ma non ha nessun ingresso abilitato.")
        if len(cth) > 1 and len(cth) != len(cch):
            avvisi.append("%d soglie CFD per %d ingressi: viene replicata l'ultima."
                          % (len(cth), len(cch)))

    return errori, avvisi


# Quanto si aspetta che la DAQ chiuda dopo SIGTERM prima di dire che non
# risponde. Nelle prove esce in un secondo; trenta sono larghi apposta, perche'
# la chiusura comprime il file e un file grosso ci mette.
ATTESA_ARRESTO_S = 30

# Sequenze di colore con cui la DAQ decora l'uscita.
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Le righe di log vere e proprie cominciano con una di queste etichette.
# Servono a staccare il contatore degli eventi da cio' che gli finisce
# incollato dietro: la DAQ non va a capo dopo il contatore, quindi il
# messaggio successivo si attacca in coda alla stessa riga.
PREFISSI = re.compile(r"(?=(?:Summary|Warning|Error|Debug)\s*:|\[INFO\])")


def righe_terminale(testo):
    """Il testo come lo mostrerebbe un terminale.

    La DAQ riscrive il contatore degli eventi sulla stessa riga usando \r.
    Convertire ogni \r in un a capo, come facevo prima, produce una riga per
    ogni evento: dopo mille eventi il riquadro del log e' mille volte la
    stessa frase. Un terminale invece tiene solo l'ULTIMO segmento fra due
    a capo, ed e' quello che si vuole vedere qui.
    """
    righe = []
    for blocco in ANSI.sub("", testo).split("\n"):
        blocco = blocco.split("\r")[-1]
        for pezzo in PREFISSI.split(blocco):
            if pezzo.strip():
                righe.append(pezzo.rstrip())
    return righe


# ---------------------------------------------------------------------------
#  Processi
# ---------------------------------------------------------------------------
def trova_daq():
    """PID della DAQ in esecuzione, oppure None.

    Si identifica dall'ESEGUIBILE, letto da /proc/<pid>/exe, non cercando una
    stringa nella riga di comando. Il motivo e' concreto: una qualunque shell
    che contenga "DAQ-WC" fra i suoi argomenti verrebbe scambiata per la DAQ,
    e un segnale finirebbe a lei. E' gia' successo, e la shell in questione
    ignorava SIGINT, cosi' l'arresto sembrava non funzionare.
    """
    for voce in os.listdir("/proc"):
        if not voce.isdigit():
            continue
        try:
            exe = os.readlink("/proc/%s/exe" % voce)
        except OSError:
            continue
        # Dopo una ricompilazione il link diventa "<percorso> (deleted)": il
        # processo vecchio e' ancora quello, e va comunque trovato.
        if exe == BINARIO or exe == BINARIO + " (deleted)":
            return int(voce)
    return None


def avvio_processo(pid):
    """Istante di avvio del processo, in secondi epoch."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            campi = f.read().rsplit(") ", 1)[1].split()
        tick = float(campi[19]) / os.sysconf("SC_CLK_TCK")
        with open("/proc/uptime") as f:
            uptime = float(f.read().split()[0])
        return time.time() - (uptime - tick)
    except (OSError, IndexError, ValueError):
        return None


# ---------------------------------------------------------------------------
#  Registro delle azioni
# ---------------------------------------------------------------------------
def registra(chi, da, azione, esito):
    """Chi ha fatto cosa. Non e' autenticazione: e' per sapere chi ha fermato
    la tua run quando siete in piu' persone sulla stessa macchina."""
    voce = {"quando": time.strftime("%Y-%m-%d %H:%M:%S"),
            "chi": chi or "(anonimo)", "da": da, "azione": azione, "esito": esito}
    try:
        os.makedirs(os.path.dirname(AZIONI), exist_ok=True)
        with open(AZIONI, "a") as f:
            f.write(json.dumps(voce) + "\n")
    except OSError:
        pass
    return voce


def ultime_azioni(n=12):
    try:
        with open(AZIONI) as f:
            righe = f.readlines()[-n:]
        return [json.loads(r) for r in righe if r.strip()]
    except (OSError, ValueError):
        return []


# ---------------------------------------------------------------------------
#  Stato
# ---------------------------------------------------------------------------
class Controllo:
    def __init__(self, toml, log_path):
        self.toml = os.path.abspath(toml)
        self.log_path = log_path
        self.lock = threading.Lock()
        self.storia = []          # (istante, eventi) per il rate recente

    # -- configurazione ----------------------------------------------------
    def config(self):
        """Riassunto in sola lettura delle chiavi che si cambiano davvero.

        In fase 1 la pagina non scrive il TOML: lo mostra. Scriverlo e' il
        passo dopo, e va fatto con modifiche chirurgiche che preservino i
        commenti, che in questo file sono documentazione.
        """
        try:
            import tomllib
            with open(self.toml, "rb") as f:
                d = tomllib.load(f)
        except Exception as e:
            return {"errore": "%s: %s" % (os.path.basename(self.toml), e)}

        g = d.get("digitizer", {})
        c = d.get("cfd", {})
        out = {
            "file": self.toml,
            "campionamento": g.get("SamplingRate"),
            "post_trigger": g.get("PostTriggerSize"),
            "canali": g.get("ChannelList"),
            "eventi_richiesti": g.get("NEvents"),
            "trigger_esterno": bool(g.get("ExternalTrigger")),
            "self_trigger": bool(g.get("SelfTrigger")),
            "self_offset": g.get("SelfTriggerThresholdOffset"),
            "self_canali": g.get("SelfTriggerChannels"),
            "collegamento": g.get("Connection"),
            "cartella_dati": g.get("OutputDir"),
        }
        if c:
            out["cfd"] = {"attivo": bool(c.get("Enabled")),
                          "soglie_mv": c.get("Threshold"),
                          "canali": c.get("Channels"),
                          "base": hex(c.get("BaseAddress", 0))}
        return out

    # -- configurazione modificabile ---------------------------------------
    def campi(self):
        """Le chiavi modificabili, con il valore che hanno adesso nel file.

        Il mtime torna insieme ai valori e va rimandato indietro quando si
        salva: se nel frattempo il file e' cambiato -- un collega dalla stessa
        pagina, o qualcuno con l'editor -- la scrittura viene rifiutata invece
        di sovrascrivere in silenzio modifiche che non si sono viste.
        """
        try:
            testo = io.open(self.toml, encoding="utf-8").read()
            mtime = os.path.getmtime(self.toml)
        except OSError as e:
            return {"errore": str(e)}

        presenti = tomledit.chiavi(testo)
        fuori = []
        for sezione, chiave, etichetta, tipo, dettagli in CAMPI:
            grezzo = presenti.get((sezione, chiave))
            fuori.append({
                "sezione": sezione, "chiave": chiave, "etichetta": etichetta,
                "tipo": tipo, "dettagli": dettagli,
                "presente": grezzo is not None,
                "valore": _ui_da_toml(tipo, grezzo) if grezzo is not None else "",
                "a_caldo": (sezione, chiave) in A_CALDO,
                "tabella": TABELLA.get((sezione, chiave)),
            })
        return {"file": self.toml, "mtime": mtime, "campi": fuori}

    def scrivi_config(self, modifiche, mtime_atteso):
        """Applica le modifiche al TOML. Torna (esito, messaggio, diff).

        Nessuna scrittura parziale: o si applica tutto o non si tocca niente.
        """
        with self.lock:
            try:
                testo = io.open(self.toml, encoding="utf-8").read()
                mtime = os.path.getmtime(self.toml)
            except OSError as e:
                return False, "Non riesco a leggere %s: %s" % (self.toml, e), []

            if mtime_atteso is not None and abs(mtime - float(mtime_atteso)) > 0.001:
                return False, ("Il file e' cambiato da quando hai aperto la pagina. "
                               "Ricarica e rifai le modifiche: non lo sovrascrivo."), []

            spec = {(s, c): (e, t, d) for s, c, e, t, d in CAMPI}
            richieste = []
            for sezione, chiave, valore in modifiche:
                if (sezione, chiave) not in spec:
                    return False, "[%s] %s non e' modificabile da qui." % (sezione, chiave), []
                etichetta, tipo, dettagli = spec[(sezione, chiave)]
                try:
                    richieste.append((sezione, chiave,
                                      _toml_da_ui(tipo, valore, dettagli, etichetta)))
                except ValueError as e:
                    return False, str(e), []

            try:
                nuovo, diff = tomledit.sostituisci(testo, richieste)
            except KeyError as e:
                return False, str(e).strip("'"), []

            if not diff:
                return True, "Niente da cambiare.", []

            # Il controllo che conta: il file che sto per scrivere e' ancora
            # TOML valido? Se no non lo scrivo affatto, invece di scoprirlo al
            # prossimo avvio della DAQ.
            avvisi = []
            try:
                import tomllib
                d = tomllib.loads(nuovo)
            except ImportError:
                d = None
            except Exception as e:
                return False, "La modifica produrrebbe un TOML non valido: %s" % e, []

            if d is not None:
                # I singoli campi erano gia' validi: qui si guarda l'insieme,
                # che e' dove stanno le combinazioni che fanno perdere la
                # serata senza che nessun valore sia sbagliato di per se'.
                errori, avvisi = coerenza(d)
                if errori:
                    return False, "Configurazione incoerente:\n" + "\n".join(
                        "- " + e for e in errori), []

            # Il nome deve essere unico anche per due salvataggi nello stesso
            # secondo: con la sola ora, il secondo backup sovrascriveva il
            # primo e si perdeva proprio la versione da cui si voleva tornare
            # indietro. E' successo alla prima prova.
            base = "%s.bak-pagina-%s" % (self.toml, time.strftime("%Y%m%d-%H%M%S"))
            backup, n = base, 1
            while os.path.exists(backup):
                backup = "%s.%d" % (base, n)
                n += 1
            try:
                io.open(backup, "w", encoding="utf-8").write(testo)
                io.open(self.toml, "w", encoding="utf-8").write(nuovo)
            except OSError as e:
                return False, "Scrittura fallita: %s" % e, []

            messaggio = "Salvato. Backup in %s" % os.path.basename(backup)
            if avvisi:
                messaggio += "\n\nDa guardare:\n" + "\n".join("- " + a for a in avvisi)
            return True, messaggio, diff

    def soglie_a_caldo(self, offsets):
        """Scrive il file di comando delle soglie del self-trigger.

        E' l'unica cosa che ha effetto sulla run IN CORSO. Non tocca il TOML:
        alla run successiva torna quello che c'e' scritto nel file, ed e'
        voluto -- uno scan non deve lasciare residui.
        """
        if not trova_daq():
            return False, "Non c'e' nessuna run in corso su cui applicarle."
        canali = self.config().get("self_canali") or []
        if not canali:
            return False, "Il TOML non dichiara SelfTriggerChannels."
        valori = [v for v in str(offsets).replace(",", " ").split() if v]
        if len(valori) == 1:
            valori = valori * len(canali)
        if len(valori) != len(canali):
            return False, ("Servono %d valori, uno per canale %s (oppure uno solo "
                           "per tutti)." % (len(canali), canali))
        try:
            righe = "".join("%d %g\n" % (int(c), float(v)) for c, v in zip(canali, valori))
        except ValueError:
            return False, "Gli offset devono essere numeri."
        percorso = os.path.join(os.path.dirname(self.log_path), "live-threshold.txt")
        cfg = self.config().get("cartella_dati")
        if cfg:
            percorso = os.path.join(cfg, "live-threshold.txt")
        try:
            io.open(percorso, "w", encoding="utf-8").write(righe)
        except OSError as e:
            return False, "Non riesco a scrivere %s: %s" % (percorso, e)
        return True, "Soglie applicate alla run in corso: %s" % righe.replace("\n", "  ").strip()

    # -- log ---------------------------------------------------------------
    def coda_log(self, n=25):
        try:
            with open(self.log_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                taglia = f.tell()
                f.seek(max(0, taglia - 60000))
                testo = f.read().decode("utf-8", "replace")
        except OSError:
            return []
        return righe_terminale(testo)[-n:]

    def testa_log(self, n=300):
        """Prime righe del log. Il file viene troncato a ogni avvio, quindi
        l'inizio e' l'inizio di QUESTA run."""
        try:
            with open(self.log_path, "rb") as f:
                testo = f.read(200000).decode("utf-8", "replace")
        except OSError:
            return []
        return righe_terminale(testo)[:n]

    def eventi_correnti(self):
        """(decodificati, richiesti, file) dal log della run.

        Il contatore si legge in coda, il nome del file in testa: quella riga
        la DAQ la stampa una volta sola all'avvio, e cercarla in fondo
        funzionava solo finche' la run era giovane. Dopo qualche centinaio di
        eventi era scorsa fuori dalla finestra e il nome spariva dalla pagina.
        """
        eventi = richiesti = None
        for riga in self.coda_log(400):
            if "Events decoded:" in riga:
                try:
                    a, b = riga.split("Events decoded:")[1].strip().split("/")
                    eventi, richiesti = int(a), int(b)
                except (ValueError, IndexError):
                    pass

        runfile = None
        for riga in self.testa_log():
            if "HDF5 output path selected:" in riga:
                runfile = riga.split("HDF5 output path selected:")[1].strip()
        return eventi, richiesti, runfile

    def stato(self):
        pid = trova_daq()
        s = {"in_corso": pid is not None, "pid": pid,
             "config": self.config(), "log": self.coda_log(25),
             "azioni": ultime_azioni(), "adesso": time.time()}
        if pid:
            avvio = avvio_processo(pid)
            s["da_secondi"] = round(time.time() - avvio, 1) if avvio else None
            ev, tot, runfile = self.eventi_correnti()
            s["eventi"] = ev
            s["eventi_richiesti"] = tot
            s["run"] = os.path.basename(runfile) if runfile else None
            s["rate"] = self._rate(ev)
        else:
            self.storia = []
        return s

    def _rate(self, eventi):
        """Rate sugli ultimi ~30 s. Il rate medio dall'inizio non serve a
        niente mentre si guarda se la run sta andando."""
        if eventi is None:
            return None
        ora = time.time()
        self.storia.append((ora, eventi))
        self.storia = [(t, e) for t, e in self.storia if ora - t <= 30]
        if len(self.storia) < 2:
            return None
        dt = self.storia[-1][0] - self.storia[0][0]
        de = self.storia[-1][1] - self.storia[0][1]
        return round(de / dt, 2) if dt > 0.5 else None

    # -- azioni ------------------------------------------------------------
    def avvia(self):
        with self.lock:
            if trova_daq():
                return False, "C'e' gia' una DAQ in esecuzione."
            if not os.path.exists(BINARIO):
                return False, "Binario non trovato: %s" % BINARIO
            if not os.path.exists(self.toml):
                return False, "Configurazione non trovata: %s" % self.toml
            try:
                log = open(self.log_path, "wb")
            except OSError as e:
                return False, "Non riesco a scrivere %s: %s" % (self.log_path, e)
            try:
                # start_new_session stacca il processo dalla sessione di questo
                # servizio: la run sopravvive al riavvio del controllore e alla
                # caduta della rete, che e' esattamente il guaio che questa
                # pagina deve togliere di mezzo.
                subprocess.Popen([BINARIO, self.toml],
                                 cwd=os.path.dirname(BINARIO),
                                 stdout=log, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL,
                                 start_new_session=True)
            except OSError as e:
                return False, "Avvio fallito: %s" % e
            finally:
                log.close()
            for _ in range(50):
                time.sleep(0.1)
                if trova_daq():
                    return True, "DAQ avviata."
            return False, "Avviata ma non la ritrovo fra i processi: guarda il log."

    def ferma(self):
        with self.lock:
            pid = trova_daq()
            if not pid:
                return False, "Non c'e' nessuna DAQ in esecuzione."
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError as e:
                return False, "Segnale fallito: %s" % e
            for i in range(ATTESA_ARRESTO_S * 2):
                time.sleep(0.5)
                if not trova_daq():
                    return True, "Run chiusa in %.1f s." % ((i + 1) * 0.5)
            return False, ("Ancora viva dopo %d s. Probabilmente il link e' "
                           "appeso: un secondo arresto la termina subito."
                           % ATTESA_ARRESTO_S)


# ---------------------------------------------------------------------------
#  Pagina
# ---------------------------------------------------------------------------
# Stringa GREZZA (r"""): le barre rovesce devono arrivare al JavaScript come
# sono scritte. Senza la r, un "\n" dentro una stringa JS diventa un a capo
# vero quando Python compone la pagina, e in JavaScript una stringa con un a
# capo dentro e' un errore di sintassi: lo script intero non parte e la pagina
# resta muta, senza che niente lo segnali.
PAGINA = r"""<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Controllo DAQ</title>
<style>
 body{font:14px/1.45 system-ui,sans-serif;margin:0;padding:18px;background:#f6f6f4;color:#1a1a19}
 h1{font-size:18px;margin:0 0 14px}
 .riga{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start}
 .box{background:#fff;border:1px solid #e2e2de;border-radius:8px;padding:14px;flex:1 1 320px}
 .box h2{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:#6b6a65;margin:0 0 10px}
 .stato{display:inline-block;padding:4px 12px;border-radius:999px;font-weight:600}
 .ferma{background:#ececea;color:#52514e}
 .corso{background:#dcefe4;color:#15603a}
 button{font:inherit;padding:9px 20px;border-radius:7px;border:1px solid transparent;cursor:pointer}
 button:disabled{opacity:.4;cursor:not-allowed}
 #avvia{background:#15603a;color:#fff}
 #ferma{background:#a8321f;color:#fff}
 table{border-collapse:collapse;width:100%}
 td{padding:3px 8px 3px 0;vertical-align:top}
 td.k{color:#6b6a65;white-space:nowrap}
 .cantab{max-height:240px;overflow:auto;border:1px solid #eee;border-radius:6px}
 .cantab table{font-size:12px}
 .cantab td{padding:2px 10px 2px 6px}
 .cantab thead td{position:sticky;top:0;background:#fafaf8;color:#6b6a65;font-weight:600}
 .cantab input[type=text]{width:58px;padding:2px 5px;font-size:12px}
 pre{background:#1a1a19;color:#e6e6e2;padding:10px;border-radius:6px;overflow:auto;
     max-height:260px;font-size:12px;margin:0;white-space:pre-wrap}
 .msg{padding:9px 12px;border-radius:6px;margin:10px 0;display:none}
 .ok{background:#dcefe4;color:#15603a}
 .ko{background:#f8e0da;color:#8a2a18}
 .az{font-size:12px;color:#52514e}
 input{font:inherit;padding:6px 9px;border:1px solid #d5d5d0;border-radius:6px}
 a{color:#2a78d6}
</style></head><body>
<h1>Controllo DAQ</h1>

<div class="box" style="margin-bottom:14px">
  <span id="badge" class="stato ferma">...</span>
  <span id="sommario" style="margin-left:14px;color:#52514e"></span>
  <div style="margin-top:14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
    <button id="avvia">Avvia run</button>
    <button id="ferma">Ferma run</button>
    <label style="margin-left:auto;color:#6b6a65">chi sei
      <input id="chi" placeholder="il tuo nome" style="width:140px">
    </label>
  </div>
  <div id="msg" class="msg"></div>
</div>

<div class="riga">
  <div class="box" style="flex:2 1 520px"><h2>Configurazione</h2>
    <div id="cfgfile" style="font-size:12px;color:#6b6a65;margin-bottom:8px"></div>
    <div id="cfg"></div>
    <div id="tabdig" style="margin-top:14px"></div>
    <div id="tabcfd" style="margin-top:14px"></div>
    <div style="margin-top:12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <button id="salva" style="background:#2a78d6;color:#fff">Salva nel TOML</button>
      <button id="ricarica" style="background:#ececea">Rileggi il file</button>
      <span style="font-size:12px;color:#6b6a65">
        vale dalla prossima run, tranne le voci segnate <b>a caldo</b>
      </span>
    </div>
    <div style="margin-top:12px;padding-top:10px;border-top:1px solid #eee">
      <label style="font-size:12px;color:#6b6a65">soglie self-trigger sulla run IN CORSO
        <input id="caldo" placeholder="es. 5  oppure  4 6" style="width:110px">
      </label>
      <button id="applica" style="background:#ececea;margin-left:6px">Applica adesso</button>
    </div>
  </div>
  <div class="box"><h2>Run in corso</h2><table id="run"></table></div>
</div>

<div class="box" style="margin-top:14px"><h2>Log della DAQ</h2><pre id="log"></pre></div>
<div class="box" style="margin-top:14px"><h2>Ultime azioni</h2><div id="azioni" class="az"></div></div>
<p style="color:#6b6a65;font-size:12px">Grafici e DQM: <a id="mon" href="#">monitor</a></p>

<script>
const $ = id => document.getElementById(id);
$("chi").value = localStorage.getItem("chi") || "";
$("chi").oninput = () => localStorage.setItem("chi", $("chi").value);
$("mon").href = location.protocol + "//" + location.hostname + ":8765/";

const PAR = new URLSearchParams(location.search);
const TOKEN = PAR.get("token") || "";

function msg(testo, ok){
  const m = $("msg"); m.textContent = testo;
  m.className = "msg " + (ok ? "ok" : "ko"); m.style.display = "block";
  setTimeout(() => { m.style.display = "none"; }, 8000);
}

function tabella(el, coppie){
  el.innerHTML = coppie.map(([k, v]) =>
    `<tr><td class="k">${k}</td><td>${v === null || v === undefined ? "&mdash;" : v}</td></tr>`
  ).join("");
}

async function azione(nome, conferma){
  if(!confirm(conferma)) return;
  $("avvia").disabled = $("ferma").disabled = true;
  try{
    const q = new URLSearchParams({chi: $("chi").value, token: TOKEN});
    const r = await fetch("/api/" + nome + "?" + q, {method: "POST"});
    const d = await r.json();
    msg(d.messaggio, d.esito);
  }catch(e){ msg("Richiesta fallita: " + e, false); }
  aggiorna();
}

$("avvia").onclick = () => azione("avvia", "Avviare una nuova run?");
$("ferma").onclick = () => azione("ferma",
  "Fermare la run in corso?\n\nLa DAQ chiude il file e resetta la board: " +
  "non si perde niente di quello che e' gia' stato acquisito.");

async function aggiorna(){
  let s;
  try{ s = await (await fetch("/api/stato?token=" + TOKEN)).json(); }
  catch(e){ $("badge").textContent = "servizio non raggiungibile"; return; }

  $("badge").textContent = s.in_corso ? "RUN IN CORSO" : "ferma";
  $("badge").className = "stato " + (s.in_corso ? "corso" : "ferma");
  $("sommario").textContent = s.in_corso
      ? (s.run || "") + (s.rate !== null && s.rate !== undefined ? "   " + s.rate + " Hz" : "")
      : "";
  $("avvia").disabled = s.in_corso;
  $("ferma").disabled = !s.in_corso;

  tabella($("run"), s.in_corso
    ? [["pid", s.pid],
       ["file", s.run],
       ["eventi", s.eventi === null ? "—" : s.eventi + " / " + s.eventi_richiesti],
       ["rate (ultimi 30 s)", s.rate === null ? "in attesa" : s.rate + " Hz"],
       ["in corso da", s.da_secondi === null ? "—" : Math.round(s.da_secondi) + " s"]]
    : [["", "nessuna run in corso"]]);

  $("log").textContent = (s.log || []).join("\n");
  $("azioni").innerHTML = (s.azioni || []).slice().reverse().map(a =>
    `${a.quando} &middot; <b>${a.chi}</b> da ${a.da}: ${a.azione} &rarr; ${a.esito}`
  ).join("<br>") || "nessuna azione registrata";
}

// --- configurazione -------------------------------------------------------
// Il form NON si ricarica col polling: riscriverebbe quello che stai
// scrivendo mentre lo scrivi. Si rilegge all'apertura, dopo un salvataggio,
// o a richiesta.
let CFG = null;

// --- tabelle per canale ---------------------------------------------------
// Due e non una: i canali del V1742 (0-31) e gli ingressi del V812 (0-15)
// sono numerazioni diverse, e la corrispondenza fra loro e' il cablaggio.
function numeri(testo){
  return (testo || "").split(/[\s,]+/).filter(x => x !== "").map(Number);
}

function campoDi(ch, chiave){ return $("t_" + chiave + "_" + ch); }

function tabellaCanali(dest, titolo, n, colonne, iniziale){
  let h = `<div style="font-size:12px;text-transform:uppercase;letter-spacing:.04em;
           color:#6b6a65;margin-bottom:4px">${titolo}</div><div class="cantab"><table>
           <thead><tr><td>ch</td>` +
           colonne.map(c => `<td>${c.titolo}</td>`).join("") + "</tr></thead><tbody>";
  for(let ch = 0; ch < n; ch++){
    h += `<tr><td style="color:#6b6a65">${ch}</td>`;
    for(const c of colonne){
      const id = "t_" + c.chiave + "_" + ch;
      if(c.tipo === "flag")
        h += `<td><input type="checkbox" id="${id}"${iniziale[c.chiave].includes(ch) ? " checked" : ""}></td>`;
      else{
        const v = iniziale[c.chiave][ch];
        h += `<td><input type="text" id="${id}" value="${v === undefined ? "" : v}"></td>`;
      }
    }
    h += "</tr>";
  }
  $(dest).innerHTML = h + "</tbody></table></div>";
}

function valoreDi(chiave){
  for(const c of CFG.campi) if(c.chiave === chiave) return c.valore;
  return "";
}

// Le soglie nel TOML sono una lista parallela ai canali abilitati, e l'ultima
// vale per tutti i rimanenti: va srotolata su ogni canale per poterla
// mostrare riga per riga, e riarrotolata al salvataggio.
function srotola(canali, soglie){
  const fuori = {};
  canali.forEach((ch, i) => { fuori[ch] = soglie[Math.min(i, soglie.length - 1)]; });
  return fuori;
}

function disegnaTabelle(){
  const reg  = numeri(valoreDi("ChannelList"));
  const self = numeri(valoreDi("SelfTriggerChannels"));
  const off  = numeri(valoreDi("SelfTriggerThresholdOffset"));
  tabellaCanali("tabdig", "canali del digitizer V1742", 32, [
    {chiave: "reg",  titolo: "registra",     tipo: "flag"},
    {chiave: "self", titolo: "self-trigger", tipo: "flag"},
    {chiave: "off",  titolo: "offset",       tipo: "testo"},
  ], {reg: reg, self: self, off: srotola(self, off)});

  const cch = numeri(valoreDi("Channels"));
  const cth = numeri(valoreDi("Threshold"));
  tabellaCanali("tabcfd", "ingressi del CFD V812  (numerazione del modulo, non del digitizer)",
    16, [
      {chiave: "cfd",  titolo: "abilitato",   tipo: "flag"},
      {chiave: "cthr", titolo: "soglia [mV]", tipo: "testo"},
    ], {cfd: cch, cthr: srotola(cch, cth)});
}

function dalleTabelle(){
  const reg = [], self = [], off = [], cch = [], cth = [];
  for(let ch = 0; ch < 32; ch++){
    if(campoDi(ch, "reg") && campoDi(ch, "reg").checked) reg.push(ch);
    if(campoDi(ch, "self") && campoDi(ch, "self").checked){
      self.push(ch);
      off.push((campoDi(ch, "off").value || "").trim());
    }
  }
  for(let ch = 0; ch < 16; ch++){
    if(campoDi(ch, "cfd") && campoDi(ch, "cfd").checked){
      cch.push(ch);
      cth.push((campoDi(ch, "cthr").value || "").trim());
    }
  }
  return {ChannelList: reg.join(", "), SelfTriggerChannels: self.join(", "),
          SelfTriggerThresholdOffset: off.join(", "),
          Channels: cch.join(", "), Threshold: cth.join(", ")};
}

function campoHtml(c){
  if(c.tabella) return "";   // sta in una tabella, non nel form
  const id = "f_" + c.sezione + "_" + c.chiave;
  const marchio = c.a_caldo ? ' <span style="color:#15603a;font-size:11px">a caldo</span>' : "";
  if(!c.presente)
    return `<tr><td class="k">${c.etichetta}</td><td style="color:#a8321f">non c'e' nel file: aggiungila a mano</td></tr>`;
  let campo;
  if(c.tipo === "booleano" || c.tipo === "scelta"){
    const opz = c.tipo === "booleano" ? ["true","false"] : c.dettagli;
    campo = `<select id="${id}">` + opz.map(o =>
      `<option${o === c.valore ? " selected" : ""}>${o}</option>`).join("") + "</select>";
  }else{
    const largo = (c.tipo === "lista" || c.tipo === "testo") ? 180 : 110;
    campo = `<input id="${id}" value="${c.valore}" style="width:${largo}px">`;
  }
  return `<tr><td class="k">${c.etichetta}${marchio}</td><td>${campo}</td></tr>`;
}

async function caricaConfig(){
  try{ CFG = await (await fetch("/api/config?token=" + TOKEN)).json(); }
  catch(e){ $("cfg").textContent = "non riesco a leggere la configurazione"; return; }
  if(CFG.errore){ $("cfg").textContent = CFG.errore; return; }
  $("cfgfile").textContent = CFG.file;
  let html = "", sez = null;
  for(const c of CFG.campi){
    if(c.sezione !== sez){
      if(sez !== null) html += "</table>";
      html += `<div style="margin:10px 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#6b6a65">[${c.sezione}]</div><table>`;
      sez = c.sezione;
    }
    html += campoHtml(c);
  }
  $("cfg").innerHTML = html + "</table>";
  disegnaTabelle();
}

function valoreCampo(c){
  const el = $("f_" + c.sezione + "_" + c.chiave);
  return el ? el.value.trim() : null;
}

$("ricarica").onclick = caricaConfig;

$("salva").onclick = async () => {
  if(!CFG) return;
  const mod = [];
  const tab = dalleTabelle();
  for(const c of CFG.campi){
    if(!c.presente) continue;
    const v = c.tabella ? tab[c.chiave] : valoreCampo(c);
    if(v !== null && v !== undefined && v !== c.valore)
      mod.push([c.sezione, c.chiave, v]);
  }
  if(!mod.length){ msg("Nessuna modifica da salvare.", true); return; }
  const elenco = mod.map(m => "  " + m[1] + "  ->  " + m[2]).join("\n");
  if(!confirm("Scrivere nel TOML?\n\n" + elenco +
              "\n\nVale dalla prossima run. Viene fatto un backup.")) return;
  $("salva").disabled = true;
  try{
    const r = await fetch("/api/config?token=" + TOKEN, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({mtime: CFG.mtime, chi: $("chi").value, modifiche: mod})
    });
    const d = await r.json();
    msg(d.messaggio, d.esito);
  }catch(e){ msg("Salvataggio fallito: " + e, false); }
  $("salva").disabled = false;
  caricaConfig();
};

$("applica").onclick = async () => {
  const v = $("caldo").value.trim();
  if(!v){ msg("Scrivi gli offset da applicare.", false); return; }
  if(!confirm("Applicare le soglie " + v + " alla run IN CORSO?\n\n" +
              "Il TOML non viene toccato: alla prossima run tornano quelle del file.")) return;
  try{
    const q = new URLSearchParams({offsets: v, chi: $("chi").value, token: TOKEN});
    const d = await (await fetch("/api/soglie?" + q, {method: "POST"})).json();
    msg(d.messaggio, d.esito);
  }catch(e){ msg("Richiesta fallita: " + e, false); }
};

caricaConfig();
aggiorna();
setInterval(aggiorna, 2000);
</script></body></html>
"""


def crea_handler(ctrl, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _manda(self, codice, tipo, corpo):
            self.send_response(codice)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def _json(self, dato, codice=200):
            self._manda(codice, "application/json", json.dumps(dato).encode())

        def _autorizzato(self, qs):
            if not token:
                return True
            dato = qs.get("token", [""])[0] or self.headers.get("X-Token", "")
            return dato == token

        def do_GET(self):
            parti = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parti.query)
            if parti.path == "/":
                return self._manda(200, "text/html; charset=utf-8", PAGINA.encode())
            if parti.path in ("/api/stato", "/api/config"):
                if not self._autorizzato(qs):
                    return self._json({"errore": "token mancante o sbagliato"}, 403)
                return self._json(ctrl.stato() if parti.path == "/api/stato"
                                  else ctrl.campi())
            self._manda(404, "text/plain", b"not found")

        def do_POST(self):
            parti = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parti.query)
            if not self._autorizzato(qs):
                return self._json({"esito": False, "messaggio": "token mancante o sbagliato"}, 403)

            chi = qs.get("chi", [""])[0].strip()[:40]
            da = self.client_address[0]
            extra = {}

            if parti.path == "/api/avvia":
                esito, messaggio = ctrl.avvia()
                registra(chi, da, "avvia", messaggio)

            elif parti.path == "/api/ferma":
                esito, messaggio = ctrl.ferma()
                registra(chi, da, "ferma", messaggio)

            elif parti.path == "/api/config":
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    corpo = json.loads(self.rfile.read(n) or b"{}")
                except (ValueError, OSError) as e:
                    return self._json({"esito": False, "messaggio": "richiesta illeggibile: %s" % e})
                chi = (corpo.get("chi") or chi).strip()[:40]
                modifiche = [(m[0], m[1], m[2]) for m in corpo.get("modifiche", [])]
                esito, messaggio, diff = ctrl.scrivi_config(modifiche, corpo.get("mtime"))
                extra["diff"] = diff
                if diff:
                    registra(chi, da, "config",
                             "; ".join("%s %s->%s" % (d["chiave"], d["da"], d["a"]) for d in diff))
                elif not esito:
                    registra(chi, da, "config", "RIFIUTATA: " + messaggio)

            elif parti.path == "/api/soglie":
                esito, messaggio = ctrl.soglie_a_caldo(qs.get("offsets", [""])[0])
                registra(chi, da, "soglie a caldo", messaggio)

            else:
                return self._manda(404, "text/plain", b"not found")

            risposta = {"esito": esito, "messaggio": messaggio}
            risposta.update(extra)
            self._json(risposta)

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", default=os.path.join(ROOT, "config", "run-local.toml"),
                    help="TOML con cui avviare le run")
    ap.add_argument("-p", "--port", type=int, default=8766,
                    help="porta (8765 e' del monitor)")
    ap.add_argument("-b", "--bind", default="127.0.0.1",
                    help="0.0.0.0 per renderlo raggiungibile dalla rete: serve "
                         "sia dalla VPN sia da un collegamento diretto")
    ap.add_argument("--token", default=os.environ.get("DAQ_CONTROL_TOKEN", ""),
                    help="se impostato, va passato come ?token=... Non e' "
                         "autenticazione vera, e' una cintura in piu' quando la "
                         "pagina e' raggiungibile da fuori")
    ap.add_argument("--log", default=os.path.join(ROOT, "data", "daq-console.log"),
                    help="dove finisce l'uscita della DAQ")
    args = ap.parse_args()

    ctrl = Controllo(args.config, args.log)
    stampa = lambda s: print(s, flush=True)
    stampa("Configurazione : %s" % ctrl.toml)
    stampa("Binario        : %s%s" % (BINARIO, "" if os.path.exists(BINARIO) else "   NON ESISTE"))
    stampa("Uscita DAQ     : %s" % args.log)
    if args.token:
        stampa("Token          : attivo")
    pid = trova_daq()
    stampa("DAQ            : %s" % ("in esecuzione, pid %d" % pid if pid else "ferma"))

    try:
        server = ThreadingHTTPServer((args.bind, args.port), crea_handler(ctrl, args.token))
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        sys.exit("\nLa porta %d e' gia' in uso: c'e' gia' un controllore attivo?\n"
                 "Trova il processo con:  ss -ltnp | grep %d" % (args.port, args.port))

    stampa("\nPagina su http://%s:%d/" % (args.bind, args.port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nchiuso")


if __name__ == "__main__":
    main()
