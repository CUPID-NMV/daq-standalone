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
import hashlib
import io
import re
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tomledit
import psu_control

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BINARIO = os.path.join(ROOT, "build", "main", "DAQ-WC")
MONITOR = os.path.join(ROOT, "tools", "live_monitor.py")

# Opzioni con cui il controllore avvia il monitor. Sono quelle che si usano
# davvero: -b 0.0.0.0 perche' la pagina si guarda da un'altra macchina, e il
# resto e' la vista preferita. Si cambiano con --monitor-args.
ARGS_MONITOR = ["-b", "0.0.0.0", "--refresh", "1", "--bw", "5"]
AZIONI = os.path.join(ROOT, "data", "azioni.jsonl")

# Stato dello scan in corso. Sta su file e non in memoria come tutto il resto:
# riavviare il controllore mentre uno scan va avanti non deve perderne le
# tracce, e un processo che nessuno sorveglia piu' e' peggio di nessun processo.
SCAN_STATO = os.path.join(ROOT, "data", "scan-in-corso.json")
SCAN_LOG = os.path.join(ROOT, "data", "scan-console.log")
GRAFICI = os.path.join(ROOT, "plots")

# La coda delle run. Come lo stato dello scan, sta su file: il controllore si
# puo' riavviare, la coda no -- e una coda che sparisce a meta' e' peggio di
# una coda che non c'e'.
CODA = os.path.join(ROOT, "data", "coda.json")

SCAN = {
    "v1742": {
        "script": os.path.join(ROOT, "tools", "scan_v1742.sh"),
        "etichetta": "self-trigger V1742",
        "opzione": "-o",
        "serve_run": True,   # cambia le soglie a caldo: la run deve esserci
    },
    "v812": {
        "script": os.path.join(ROOT, "tools", "scan_v812.sh"),
        "etichetta": "CFD V812",
        "opzione": "-t",
        "serve_run": False,  # ogni punto e' una run sua: la DAQ deve essere ferma
    },
}

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
    ("digitizer", "SamplingRate",    "sampling rate",      "scelta", ["5GHz", "2.5GHz", "1GHz", "750MHz"]),
    ("digitizer", "RecordLength",    "samples per event",  "intero", (1, 1024)),
    ("digitizer", "PostTriggerSize", "post-trigger [%]",   "intero", (0, 100)),
    ("digitizer", "NEvents",         "events to acquire",  "intero", (1, 10**9)),
    ("digitizer", "TailCut",         "trailing samples dropped", "intero", (0, 200)),
    ("digitizer", "ChannelList",     "recorded channels",  "lista",  (0, 31)),
    ("digitizer", "DCOffset",        "DC offset (higher = lower baseline)", "intero", (0, 65535)),
    ("digitizer", "Connection",      "link",               "scelta", ["auto", "ETH_V4718", "USB_A4818"]),
    ("digitizer", "DRS4Correction",  "DRS4 corrections",   "booleano", None),
    ("digitizer", "OutputFile",      "file prefix",        "testo",  None),
    ("digitizer", "OutputDir",       "output directory",   "testo",  None),

    ("digitizer", "ExternalTrigger", "external trigger (TRG-IN)", "booleano", None),
    ("digitizer", "IOLevel",         "front panel level",  "scelta", ["NIM", "TTL"]),
    ("digitizer", "TriggerPolarity", "discrimination edge (0 = rising/positive pulses, 1 = falling/negative)", "intero", (0, 1)),
    ("digitizer", "SelfTrigger",     "self-trigger",       "booleano", None),
    ("digitizer", "SelfTriggerMode", "self-trigger mode",  "scelta", ["paired", "global"]),
    ("digitizer", "SelfTriggerChannels", "self-trigger channels", "lista", (0, 31)),
    ("digitizer", "SelfTriggerThresholdOffset", "threshold offset", "lista", (0, 4095)),
    ("digitizer", "TriggerOut",      "what comes out of TRG-OUT", "scelta", ["self", "all", "off", "default"]),

    ("cfd", "Enabled",   "V812 CFD enabled",    "booleano", None),
    ("cfd", "Threshold", "CFD thresholds [mV]", "lista", (5, 255)),
    ("cfd", "Channels",  "CFD inputs used",     "lista", (0, 15)),
    ("cfd", "Width",     "output width [counts]", "intero", (0, 255)),
    ("cfd", "DeadTime",  "dead time [counts]",    "intero", (0, 255)),
    ("cfd", "Majority",  "majority (1 = OR)", "intero", (1, 20)),

    ("settings", "verbosity", "on-screen verbosity", "intero", (0, 4)),
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
            raise ValueError("%s: only true and false are allowed" % etichetta)
        return v
    if tipo == "scelta":
        if v not in dettagli:
            raise ValueError("%s: allowed values are %s" % (etichetta, ", ".join(dettagli)))
        return '"%s"' % v
    if tipo == "testo":
        if '"' in v:
            raise ValueError("%s: no quotes inside the value" % etichetta)
        return '"%s"' % v
    if tipo == "intero":
        # int(v, 0) accetta anche 0x...: DCOffset si scrive in esadecimale e
        # convertirlo in decimale renderebbe il file meno leggibile, oltre a
        # far sembrare "cambiata" una riga che non lo e'.
        try:
            n = int(v, 0)
        except ValueError:
            raise ValueError("%s: an integer is required" % etichetta)
        lo, hi = dettagli
        if not lo <= n <= hi:
            raise ValueError("%s: out of range, allowed from %d to %d" % (etichetta, lo, hi))
        return v
    if tipo == "lista":
        pezzi = [p for p in v.replace(",", " ").split() if p]
        if not pezzi:
            raise ValueError("%s: the list is empty" % etichetta)
        numeri = []
        lo, hi = dettagli
        for p in pezzi:
            try:
                n = float(p) if "." in p else int(p)
            except ValueError:
                raise ValueError("%s: '%s' is not a number" % (etichetta, p))
            if not lo <= n <= hi:
                raise ValueError("%s: %s out of range, allowed from %d to %d"
                                 % (etichetta, p, lo, hi))
            numeri.append(p)
        return "[" + ", ".join(numeri) + "]"
    raise ValueError("unknown type: %s" % tipo)


# Latenza del self-trigger, in nanosecondi: e' il tempo fra il superamento
# della soglia e l'arresto del DRS4, e sposta l'impulso dentro la finestra.
LATENZA_NS = {"paired": 320.0, "global": 420.0}
PASSO_NS = {"5GHz": 0.2, "2.5GHz": 0.4, "1GHz": 1.0, "750MHz": 1.333}


# Porta su cui gira il monitor. Il link nella pagina e la sonda qui sotto
# devono puntare allo stesso posto, quindi il numero sta scritto una volta
# sola.
PORTA_MONITOR = 8765

# Esito dell'ultima sonda, con l'istante: la pagina chiede lo stato ogni paio
# di secondi e aprire una connessione a ogni giro non serve a niente.
_monitor_visto = (0.0, False)


def monitor_acceso(porta=None):
    """True se qualcuno ascolta sulla porta del monitor, su questa macchina.

    La domanda che risponde e' "il link al monitor porta da qualche parte?".
    Il link apre una pagina, non avvia niente: senza il processo acceso
    l'utente trova un errore del browser e non ha modo di sapere se il
    problema e' la rete, la VPN o il monitor che non e' stato lanciato.

    Si prova una connessione TCP e basta, senza richiesta HTTP: interessa che
    la porta risponda, e una GET costringerebbe il monitor a rigenerare roba
    a ogni giro di polling.
    """
    global _monitor_visto
    quando, esito = _monitor_visto
    if time.time() - quando < 3.0:
        return esito
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.3)
    try:
        esito = sock.connect_ex(("127.0.0.1", porta or PORTA_MONITOR)) == 0
    except OSError:
        esito = False
    finally:
        sock.close()
    _monitor_visto = (time.time(), esito)
    return esito


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
        errori.append("ChannelList is empty: nothing would be recorded.")

    # La cartella di uscita: la DAQ non la crea, si rifiuta di partire con
    # "Output directory does not exist" dopo aver gia' aperto il collegamento.
    # Meglio dirlo qui, che costa una chiamata a stat.
    od = g.get("OutputDir")
    if od is not None:
        od = str(od)
        if not os.path.isabs(od):
            errori.append("OutputDir must be an absolute path: the DAQ runs from "
                          "build/, so a relative path would not point where you think.")
        elif not os.path.isdir(od):
            errori.append("OutputDir does not exist: %s. The DAQ does not create it "
                          "and would refuse to start." % od)
        elif not os.access(od, os.W_OK):
            errori.append("OutputDir is not writable: %s." % od)

    self_on = bool(g.get("SelfTrigger"))
    est_on = bool(g.get("ExternalTrigger"))
    modo = str(g.get("SelfTriggerMode", "paired"))
    freq = str(g.get("SamplingRate", ""))

    if not self_on and not est_on and not c.get("Enabled"):
        avvisi.append("Neither self-trigger nor external trigger: only the software "
                      "trigger would be left, and the run would acquire nothing on its own.")

    # Due sorgenti accese insieme sono previste dalla board e vanno in OR: non
    # e' un errore. Ma negli eventi non resta scritto QUALE ha fatto scattare
    # il trigger, quindi da quel momento ogni rate misurato e' il rate dell'OR
    # e non si puo' piu' attribuire. E' la stessa ragione per cui lo scan del
    # V812 si rifiuta di partire col self-trigger acceso.
    if self_on and est_on:
        chi = "TRG-IN, where the CFD arrives," if c.get("Enabled") else "TRG-IN"
        avvisi.append("Self-trigger and %s are both on: the two sources go in OR. "
                      "That is legitimate, but the event keeps no record of which one "
                      "fired, so any measured rate is the rate of the OR. "
                      "To measure one alone, switch the other off." % chi)

    # Con il self-trigger spento NON si avvisa di niente che lo riguardi --
    # ne' TriggerOut = "self", ne' i canali, ne' gli offset rimasti scritti nel
    # pannello. Quelle chiavi restano nel TOML da una run all'altra ed e'
    # giusto che restino: avvisare ogni volta trasformerebbe il riquadro in
    # rumore di fondo, e un riquadro che avvisa sempre non lo legge piu'
    # nessuno. Quando il self-trigger e' acceso i controlli tornano, sotto.

    # TailCut scarta i campioni finali: se arriva a mangiarsi tutto l'evento,
    # la run scrive forme d'onda di lunghezza zero.
    rl0, tc = g.get("RecordLength"), g.get("TailCut")
    if isinstance(rl0, int) and isinstance(tc, int) and tc >= rl0:
        errori.append("TailCut (%d) would drop all %d samples of the event: "
                      "nothing would be left to save." % (tc, rl0))

    if self_on:
        sch = lista(g.get("SelfTriggerChannels")) or canali
        fuori = [x for x in sch if x not in canali]
        if fuori:
            avvisi.append("Channels in self-trigger but not recorded: %s. Legitimate if "
                          "you want to trigger on one and look at another, wrong if "
                          "that is not what you meant." % fuori)
        off = lista(g.get("SelfTriggerThresholdOffset"))
        if len(off) > 1 and len(off) != len(sch):
            avvisi.append("%d thresholds for %d self-trigger channels: the DAQ repeats "
                          "the last one on the rest." % (len(off), len(sch)))

        if freq == "5GHz":
            errori.append("At 5 GHz the self-trigger cannot work: the window is shorter "
                          "than the latency, the pulse always falls outside.")
        elif freq == "2.5GHz" and modo == "global":
            avvisi.append("At 2.5 GHz in global mode the latency (~420 ns) exceeds the "
                          "window (410 ns): the pulse risks falling outside. "
                          "In paired mode it works.")

        # Dove cade l'impulso nella finestra. E' il conto che ci ha gia' fatto
        # registrare eventi vuoti senza capire perche'.
        passo = PASSO_NS.get(freq)
        rl = g.get("RecordLength")
        pt = g.get("PostTriggerSize")
        if passo and isinstance(rl, int) and isinstance(pt, (int, float)):
            finestra = rl * passo
            pos = (1.0 - pt / 100.0) * finestra - LATENZA_NS.get(modo, 320.0)
            if pos < 0:
                errori.append("With these values the pulse would fall %.0f ns BEFORE the "
                              "start of the window: lower the post-trigger or slow "
                              "down the sampling." % (-pos))
            elif pos > finestra * 0.9:
                avvisi.append("The pulse would fall at %.0f ns in a %.0f ns window: too "
                              "close to the end, you risk clipping its tail."
                              % (pos, finestra))

    # Verso del fronte contro verso del piedistallo. Sono due chiavi lontane
    # nel file e indipendenti nel codice, ma descrivono lo stesso segnale: con
    # DCOffset alto il piedistallo sta in fondo alla dinamica, quindi lo spazio
    # per l'impulso e' VERSO L'ALTO e il fronte da discriminare e' quello di
    # salita. La combinazione sbagliata non da' alcun errore: la soglia viene
    # messa qualche conteggio dalla parte dove il segnale non va mai, e la run
    # acquisisce zero eventi senza lamentarsi. E' costata una run di 52 s a
    # vuoto con il SiPM (piedistallo 979 in Transparent Mode, soglia 974,
    # impulso positivo). Il confine e' 0x9000: li' il piedistallo misurato
    # passa per meta' dinamica (0x7000 -> 3185 conteggi, 0xA000 -> 1436).
    # Resta un avviso e non un errore perche' DCOffset e' per canale mentre
    # TriggerPolarity e' uno solo: con polarita' mescolate un compromesso e'
    # inevitabile.
    pol = g.get("TriggerPolarity")
    dco = [x for x in lista(g.get("DCOffset")) if isinstance(x, int)]
    if pol in (0, 1) and dco:
        if pol == 1 and min(dco) > 0x9000:
            avvisi.append("TriggerPolarity = 1 (falling edge) but DCOffset is %s: the "
                          "baseline sits at the BOTTOM of the range, so there is room "
                          "only for positive pulses. The threshold would end up a few "
                          "counts below a baseline the signal never goes below, and the "
                          "run would record nothing. Use 0 for positive pulses."
                          % ", ".join(hex(x) for x in dco))
        elif pol == 0 and max(dco) < 0x8000:
            avvisi.append("TriggerPolarity = 0 (rising edge) but DCOffset is %s: the "
                          "baseline sits HIGH, which is the setting for negative pulses. "
                          "Use 1 for those, or raise DCOffset (higher = lower baseline)."
                          % ", ".join(hex(x) for x in dco))

    if str(g.get("IOLevel", "NIM")).upper() == "TTL" and c.get("Enabled"):
        avvisi.append("Front panel set to TTL but the V812 CFD is on: its OR output "
                      "is standard NIM, so it would not be read. Either set NIM or "
                      "put a converter in between.")

    if c.get("Enabled"):
        if str(g.get("Connection", "")) == "USB_A4818":
            errori.append("With the CFD on the bridge is needed: on USB_A4818 the fibre "
                          "goes straight to the digitizer and the VME bus has no master.")
        if not est_on:
            avvisi.append("CFD on but external trigger off: the V812 OR comes in through "
                          "TRG-IN, so as it is, it does nothing.")
        cch = lista(c.get("Channels"))
        cth = lista(c.get("Threshold"))
        if not cch:
            errori.append("The CFD is on but has no input enabled.")
        if len(cth) > 1 and len(cth) != len(cch):
            avvisi.append("%d CFD thresholds for %d inputs: the last one is repeated."
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


def trova_monitor():
    """PID del monitor in esecuzione, oppure None.

    Come per la DAQ non si cerca una stringa nella riga di comando: si chiede
    a /proc che l'eseguibile sia un python e che il PRIMO argomento sia
    proprio live_monitor.py. Cercare "live_monitor.py" fra tutti gli
    argomenti prende anche la shell che lo ha lanciato -- e un SIGTERM a
    quella lascia il monitor vivo e orfano. E' successo oggi.
    """
    for voce in os.listdir("/proc"):
        if not voce.isdigit():
            continue
        try:
            exe = os.readlink("/proc/%s/exe" % voce)
            argv = open("/proc/%s/cmdline" % voce).read().split("\0")
        except OSError:
            continue
        if not os.path.basename(exe).startswith("python"):
            continue
        if len(argv) > 1 and os.path.basename(argv[1]) == "live_monitor.py":
            return int(voce)
    return None


def elenco_grafici(n=12):
    """I grafici piu' recenti prodotti dagli scan."""
    try:
        nomi = [f for f in os.listdir(GRAFICI) if f.endswith(".png")]
    except OSError:
        return []
    fuori = []
    for nome in nomi:
        try:
            st = os.stat(os.path.join(GRAFICI, nome))
        except OSError:
            continue
        fuori.append({"nome": nome, "quando": st.st_mtime, "byte": st.st_size})
    fuori.sort(key=lambda x: -x["quando"])
    return fuori[:n]


def elenco_misure(n=20):
    """Le misure di scan disponibili: i JSON, non i PNG gia' disegnati.

    Si elencano quelle perche' il grafico lo si rifa' al momento, con la scala
    che si vuole: un PNG e' una decisione gia' presa, un JSON no.
    """
    try:
        nomi = [f for f in os.listdir(GRAFICI)
                if f.endswith(".json") and ("scan" in f)]
    except OSError:
        return []
    fuori = []
    for nome in nomi:
        try:
            st = os.stat(os.path.join(GRAFICI, nome))
            with open(os.path.join(GRAFICI, nome)) as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        fuori.append({"nome": nome, "quando": st.st_mtime,
                      "punti": len(d.get("punti", [])),
                      "tipo": "V812 CFD" if d.get("tipo") == "v812" else "V1742 self-trigger",
                      "canali": d.get("canali")})
    fuori.sort(key=lambda x: -x["quando"])
    return fuori[:n]


def disegna_misure(nomi, logy, per_canale=False, canali="", mv_per_offset=""):
    """Lancia plot_scan.py sulle misure scelte. Torna (esito, messaggio, png)."""
    scelti = []
    for nome in nomi[:8]:
        if not nome or "/" in nome or not nome.endswith(".json"):
            return False, "Nome non valido: %s" % nome, None
        percorso = os.path.realpath(os.path.join(GRAFICI, nome))
        if os.path.dirname(percorso) != os.path.realpath(GRAFICI) or not os.path.isfile(percorso):
            return False, "Misura non trovata: %s" % nome, None
        scelti.append(percorso)
    if not scelti:
        return False, "No measurement selected.", None

    # Nome ricavato dalla selezione, non dall'ora: due disegni nello stesso
    # secondo avrebbero avuto lo stesso nome e si sarebbero sovrascritti a
    # vicenda, e un PNG nuovo a ogni clic riempirebbe plots/ di viste
    # identiche. Cosi' lo stesso insieme con la stessa scala e' sempre lo
    # stesso file, e il browser lo rilegge grazie al parametro anti-cache.
    firma = hashlib.sha1(("|".join(sorted(nomi)) + ("|log" if logy else "|lin") +
                          ("|ch:" + canali if per_canale else "|or") +
                          ("|mv:" + str(mv_per_offset or "")))
                         .encode()).hexdigest()[:8]
    uscita = os.path.join(GRAFICI, "view_%s.png" % firma)
    cmd = [sys.executable, os.path.join(ROOT, "tools", "plot_scan.py")]
    if logy:
        cmd.append("--logy")
    if per_canale:
        cmd.append("--per-channel")
        pulita = " ".join(x for x in canali.replace(",", " ").split() if x.isdigit())
        if pulita:
            cmd += ["--channels", pulita]
    # La conversione forzata e' l'unica via per disegnare il conteggio per
    # canale a una frequenza dove non e' stata misurata. plot_scan.py si
    # rifiuta di inventarla, e fa bene; ma la via d'uscita esisteva solo da
    # riga di comando, cosi' dalla pagina la funzione risultava "rotta".
    # Il grafico scrive da se' che quel numero e' assunto.
    if str(mv_per_offset or "").strip():
        try:
            k = float(str(mv_per_offset).replace(",", "."))
        except ValueError:
            return False, "mV/offset non e' un numero: %s" % mv_per_offset, None
        if not 0 < k < 100:
            return False, "mV/offset fuori da ogni scala plausibile: %g" % k, None
        cmd += ["--mv-per-offset", repr(k)]
    cmd += scelti + ["-o", uscita]
    # Dieci minuti e non due. Il conteggio per canale non si legge dal JSON
    # dello scan: rilegge le forme d'onda dal file della run, e a run finita
    # quel file e' COMPRESSO. Un .gz non si puo' leggere a pezzi, va
    # decompresso tutto: misurato, 195 s per una run da 2.0 GB compressi, cioe'
    # oltre il vecchio tetto di 120. Dalla pagina il disegno falliva sempre, e
    # falliva in silenzio dopo due minuti di attesa.
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return False, ("Plot timed out after 10 minutes. With per channel the "
                       "waveforms are reread from the run file, and a finished "
                       "run is compressed: that costs minutes per gigabyte."), None
    except OSError as e:
        return False, "Plot failed: %s" % e, None
    if r.returncode != 0:
        detto = (r.stderr.strip() or r.stdout.strip() or "plot_scan failed")
        # plot_scan.py parla a chi sta in un terminale, e il suo rifiuto dice
        # "usa --mv-per-offset". Qui dentro quell'opzione e' una casella, e
        # rimandare l'utente a un'opzione da riga di comando lo lascia fermo
        # davanti a una pagina che AVEVA gia' quello che serviva. Succede
        # solo per questo rifiuto, quindi si riscrive solo questo.
        if "offset -> mV non e' misurata" in detto:
            freq = re.search(r"\ba ([0-9.]+ [GM]S/s)\b", detto)
            return False, (
                "Per channel needs the mV per offset unit, and at %s it has "
                "never been measured: the conversion changes by 29%% between "
                "2.5 and 1 GS/s, so it is not interpolated. Type a number in "
                "the mV/offset box above \u2014 2.7 is the value measured at "
                "1 GS/s \u2014 and the plot will label the axis ASSUMED. "
                "Or untick per channel: the trigger-rate plot is in offset "
                "counts and needs no conversion."
                % (freq.group(1) if freq else "this sampling rate")), None
        return False, detto[-400:], None
    return True, "Plot drawn.", os.path.basename(uscita)


def percorso_grafico(nome):
    """Percorso del grafico, oppure None se il nome non e' accettabile.

    Si accetta solo un nome semplice dentro plots/, e si verifica anche il
    percorso risolto: una pagina raggiungibile dalla VPN non deve poter
    diventare un modo per leggere file qualunque della macchina.
    """
    if not nome or "/" in nome or "\\" in nome or not nome.endswith(".png"):
        return None
    percorso = os.path.realpath(os.path.join(GRAFICI, nome))
    if os.path.dirname(percorso) != os.path.realpath(GRAFICI):
        return None
    return percorso if os.path.isfile(percorso) else None


def trova_scan():
    """Lo scan in corso, oppure None.

    Non basta che il pid esista: dopo un riavvio della macchina quel numero
    puo' essere di tutt'altro processo. Si controlla che la riga di comando
    contenga ancora lo script, che e' l'unico modo onesto di riconoscerlo --
    uno script di shell non ha un eseguibile proprio da leggere in /proc, al
    contrario della DAQ.
    """
    try:
        with open(SCAN_STATO) as f:
            s = json.load(f)
    except (OSError, ValueError):
        return None
    try:
        with open("/proc/%d/cmdline" % s["pid"], "rb") as f:
            riga = f.read().decode("utf-8", "replace")
    except (OSError, KeyError, TypeError):
        riga = ""
    if s.get("script") and s["script"] in riga:
        return s
    # Finito o sparito: si toglie di mezzo, se no la pagina direbbe per sempre
    # che c'e' uno scan in corso.
    try:
        os.remove(SCAN_STATO)
    except OSError:
        pass
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
            "chi": chi or "(anonymous)", "da": da, "azione": azione, "esito": esito}
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
#  Alimentatore dei SiPM (Aim-TTi PLH250-P)
# ---------------------------------------------------------------------------
#  Il controllore lo legge ogni PSU_PERIODO_S e scrive ogni lettura su
#  data/psu-log.csv, run o non run: il guadagno di un SiPM dipende dalla
#  tensione, e quando fra mesi un gruppo di run mostrera' un guadagno
#  spostato bisognera' poter dire che tensione c'era. Il confronto con le run
#  si fa per data e ora, senza toccare la DAQ.
#
#  La tensione la cambia SOLO l'operatore, con un pulsante. Qui dentro non c'e'
#  niente che la muova da solo: ne' a fine run, ne' a fine coda.
#
#  Lo strumento accetta una connessione alla volta: ogni lettura apre, legge e
#  chiude, cosi' tools/psu_control.py da terminale continua a funzionare.
PSU_PERIODO_S = 3.0
PSU_LOG = os.path.join(ROOT, "data", "psu-log.csv")
PSU_STORIA_S = 24 * 3600
PSU_PASSO_V = 5.0          # rampa: volt per passo
PSU_ATTESA_S = 0.5         # rampa: secondi fra un passo e l'altro


class Alimentatore:
    def __init__(self, host):
        self.host = host
        self.lock = threading.Lock()      # una sola conversazione alla volta
        self.ultima = None                # ultima lettura riuscita
        self.errore = None
        self.idn = None
        self.rampa = None                 # {"da", "a", "v"} mentre sale/scende
        self._ferma_rampa = False
        self.storia = self._carica_storia()
        threading.Thread(target=self._ciclo, daemon=True).start()

    # -- lettura -----------------------------------------------------------
    def _leggi(self, psu):
        if self.idn is None:
            self.idn = psu.idn()
        return {"t": time.time(), "v_set": psu.vset(), "v_out": psu.vout(),
                "i_out": psu.iout(), "i_lim": psu.iset(), "uscita": psu.output(),
                "ovp": psu._num(psu.query("OVP1?")),
                "ocp": psu._num(psu.query("OCP1?"))}

    def _registra(self, l):
        self.ultima, self.errore = l, None
        self.storia.append((l["t"], l["v_out"], l["i_out"]))
        limite = l["t"] - PSU_STORIA_S
        if self.storia and self.storia[0][0] < limite:
            self.storia = [x for x in self.storia if x[0] >= limite]
        try:
            nuovo = not os.path.exists(PSU_LOG)
            with open(PSU_LOG, "a") as f:
                if nuovo:
                    f.write("unix_time,ora,v_set,v_out,i_out,i_lim,uscita\n")
                f.write("%.1f,%s,%.3f,%.3f,%.8f,%.6f,%d\n" % (
                    l["t"], time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(l["t"])),
                    l["v_set"], l["v_out"], l["i_out"], l["i_lim"], l["uscita"]))
        except OSError:
            pass

    def _carica_storia(self):
        """Le ultime 24 ore dal CSV: riavviare il controllore non deve
        svuotare il grafico proprio quando serve a vedere una deriva."""
        storia, limite = [], time.time() - PSU_STORIA_S
        try:
            with open(PSU_LOG, "rb") as f:
                f.seek(0, os.SEEK_END)
                # ~70 byte a riga, una riga ogni PSU_PERIODO_S: 24 ore ci stanno
                f.seek(max(0, f.tell() - int(80 * PSU_STORIA_S / PSU_PERIODO_S)))
                righe = f.read().decode("utf-8", "replace").splitlines()
        except OSError:
            return storia
        for r in righe:
            c = r.split(",")
            try:
                t = float(c[0])
                if t >= limite:
                    storia.append((t, float(c[3]), float(c[4])))
            except (ValueError, IndexError):
                pass
        return storia

    def _ciclo(self):
        while True:
            with self.lock:
                try:
                    psu = psu_control.PLH(self.host, timeout=2.0)
                    try:
                        self._registra(self._leggi(psu))
                    finally:
                        psu.close()
                except Exception as e:
                    self.errore = "%s: %s" % (self.host, e)
            time.sleep(PSU_PERIODO_S)

    # -- per la pagina -----------------------------------------------------
    def stato(self):
        return {"host": self.host, "idn": self.idn, "lettura": self.ultima,
                "errore": self.errore, "rampa": self.rampa,
                "vmax": psu_control.VMAX, "periodo": PSU_PERIODO_S}

    def serie(self, ore):
        """Storia ridotta a ~300 punti, media per intervallo. La media e non
        il campione puntuale: e' la deriva che interessa, non il rumore della
        singola lettura."""
        da = time.time() - ore * 3600
        punti = [x for x in self.storia if x[0] >= da]
        n = max(1, len(punti) // 300)
        fuori = []
        for i in range(0, len(punti), n):
            g = punti[i:i + n]
            fuori.append([round(sum(x[0] for x in g) / len(g), 1),
                          round(sum(x[1] for x in g) / len(g), 4),
                          sum(x[2] for x in g) / len(g)])
        return {"ore": ore, "punti": fuori}

    def riassunto(self):
        """Una riga per il registro delle azioni, all'avvio e all'arresto
        delle run."""
        l = self.ultima
        if not l or time.time() - l["t"] > 3 * PSU_PERIODO_S:
            return "HV not readable"
        return "HV %.2f V %.2f uA %s" % (l["v_out"], l["i_out"] * 1e6,
                                         "ON" if l["uscita"] else "OFF")

    # -- comandi -----------------------------------------------------------
    def _comando(self, fn):
        if self.rampa:
            return False, "A ramp is in progress: wait, or press Output OFF."
        with self.lock:
            try:
                psu = psu_control.PLH(self.host, timeout=2.0)
                try:
                    msg = fn(psu)
                    self._registra(self._leggi(psu))
                finally:
                    psu.close()
                return True, msg
            except (ValueError, RuntimeError) as e:
                return False, str(e)
            except OSError as e:
                return False, "Supply not answering at %s: %s" % (self.host, e)

    def uscita(self, accesa):
        if not accesa:
            # Spegnere deve funzionare SEMPRE, anche a meta' rampa: e' il
            # pulsante che si preme quando qualcosa non va.
            self._ferma_rampa = True
            with self.lock:
                try:
                    psu = psu_control.PLH(self.host, timeout=2.0)
                    try:
                        psu.send("OP1 0")
                        self._registra(self._leggi(psu))
                    finally:
                        psu.close()
                    return True, "Output OFF."
                except Exception as e:
                    return False, "Could not switch off: %s" % e
        def accendi(p):
            p.send("OP1 1")
            return "Output ON at %.2f V." % p.vset()
        return self._comando(accendi)

    def limite_corrente(self, ampere):
        def imposta(p):
            p.send("I1 %.6f" % ampere)
            return "Current limit %.1f uA." % (ampere * 1e6)
        return self._comando(imposta)

    def ovp(self, volt):
        def imposta(p):
            p.send("OVP1 %.2f" % volt)
            return "OVP set to %.2f V." % volt
        return self._comando(imposta)

    def tensione(self, volt):
        """Avvia la rampa in un thread e torna subito: la pagina segue
        l'avanzamento dallo stato."""
        try:
            psu_control.controlla_tensione(volt)
        except ValueError:
            return False, "%.2f V is outside 0-%.1f V." % (volt, psu_control.VMAX)
        if self.rampa:
            return False, "A ramp is already in progress."
        self.rampa = {"a": volt, "v": None}
        self._ferma_rampa = False
        threading.Thread(target=self._esegui_rampa, args=(volt,), daemon=True).start()
        return True, "Ramping to %.2f V." % volt

    def _esegui_rampa(self, volt):
        def passo(v):
            self.rampa["v"] = v
            self._registra(self._leggi(psu))
        try:
            with self.lock:
                psu = psu_control.PLH(self.host, timeout=2.0)
                try:
                    self.rampa["da"] = psu.vset()
                    finita = psu_control.ramp(psu, volt, PSU_PASSO_V, PSU_ATTESA_S,
                                              passo=passo,
                                              interrompi=lambda: self._ferma_rampa)
                    if not finita:
                        registra("controller", "", "psu/ramp",
                                 "interrupted at %.2f V" % (self.rampa["v"] or 0))
                finally:
                    psu.close()
        except Exception as e:
            self.errore = "ramp: %s" % e
            registra("controller", "", "psu/ramp", "FAILED: %s" % e)
        finally:
            self.rampa = None

    def trova(self):
        trovati = [(ip, idn) for ip, idn in psu_control.find(
            self.host.rsplit(".", 1)[0] + ".0", stampa=False) if "PLH" in idn]
        if not trovati:
            return False, "No PLH supply found on %s.0/24." % self.host.rsplit(".", 1)[0]
        self.host, self.idn = trovati[0][0], None
        return True, "Supply found at %s." % self.host


# ---------------------------------------------------------------------------
#  Stato
# ---------------------------------------------------------------------------
class Controllo:
    def __init__(self, toml, log_path, porta_monitor=PORTA_MONITOR,
                 args_monitor=None, psu_host=None):
        self.toml = os.path.abspath(toml)
        self.log_path = log_path
        self.lock = threading.Lock()
        self.storia = []          # (istante, eventi) per il rate recente
        self.porta_monitor = porta_monitor
        self.args_monitor = list(args_monitor or ARGS_MONITOR)
        self.psu = Alimentatore(psu_host) if psu_host else None

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
                return False, "Cannot read %s: %s" % (self.toml, e), []

            if mtime_atteso is not None and abs(mtime - float(mtime_atteso)) > 0.001:
                return False, ("The file changed since you opened the page. "
                               "Reload and redo your changes: I will not overwrite it."), []

            spec = {(s, c): (e, t, d) for s, c, e, t, d in CAMPI}
            richieste = []
            for sezione, chiave, valore in modifiche:
                if (sezione, chiave) not in spec:
                    return False, "[%s] %s cannot be changed from here." % (sezione, chiave), []
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
                return True, "Nothing to change.", []

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
                return False, "The change would produce invalid TOML: %s" % e, []

            if d is not None:
                # I singoli campi erano gia' validi: qui si guarda l'insieme,
                # che e' dove stanno le combinazioni che fanno perdere la
                # serata senza che nessun valore sia sbagliato di per se'.
                errori, avvisi = coerenza(d)
                if errori:
                    return False, "Inconsistent configuration:\n" + "\n".join(
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
                return False, "Write failed: %s" % e, []

            messaggio = "Saved. Backup in %s" % os.path.basename(backup)
            if avvisi:
                messaggio += "\n\nWorth a look:\n" + "\n".join("- " + a for a in avvisi)
            return True, messaggio, diff

    def soglie_a_caldo(self, offsets):
        """Scrive il file di comando delle soglie del self-trigger.

        E' l'unica cosa che ha effetto sulla run IN CORSO. Non tocca il TOML:
        alla run successiva torna quello che c'e' scritto nel file, ed e'
        voluto -- uno scan non deve lasciare residui.
        """
        if not trova_daq():
            return False, "There is no run in progress to apply them to."
        canali = self.config().get("self_canali") or []
        if not canali:
            return False, "The TOML does not declare SelfTriggerChannels."
        valori = [v for v in str(offsets).replace(",", " ").split() if v]
        if len(valori) == 1:
            valori = valori * len(canali)
        if len(valori) != len(canali):
            return False, ("%d values are needed, one per channel %s (or a single "
                           "one for all)." % (len(canali), canali))
        try:
            righe = "".join("%d %g\n" % (int(c), float(v)) for c, v in zip(canali, valori))
        except ValueError:
            return False, "The offsets must be numbers."
        percorso = os.path.join(os.path.dirname(self.log_path), "live-threshold.txt")
        cfg = self.config().get("cartella_dati")
        if cfg:
            percorso = os.path.join(cfg, "live-threshold.txt")
        try:
            io.open(percorso, "w", encoding="utf-8").write(righe)
        except OSError as e:
            return False, "Cannot write %s: %s" % (percorso, e)
        return True, "Thresholds applied to the running acquisition: %s" % righe.replace("\n", "  ").strip()

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
        scan = trova_scan()
        s = {"in_corso": pid is not None, "pid": pid,
             "config": self.config(), "log": self.coda_log(25),
             "azioni": ultime_azioni(), "adesso": time.time(),
             "monitor": monitor_acceso(self.porta_monitor),
             "monitor_pid": trova_monitor(),
             "monitor_porta": self.porta_monitor,
             "scan": None, "coda": self.leggi_coda(),
             "scan_log": self.coda_scan(12) if not scan else None,
             "psu": self.psu.stato() if self.psu else None}
        if scan:
            s["scan"] = {
                "avanzamento": self.avanzamento_scan(),
                "tipo": scan["tipo"],
                "etichetta": SCAN[scan["tipo"]]["etichetta"],
                "valori": scan.get("valori"),
                "secondi": scan.get("secondi"),
                "da_secondi": round(time.time() - scan.get("avviato", time.time()), 1),
                "log": self.coda_scan(),
            }
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

    # -- coda di run -------------------------------------------------------
    #
    #  Una coda e' una lista di run, ognuna con le sue modifiche al TOML e una
    #  durata facoltativa. L'esecutore e' un thread di questo processo, ma lo
    #  STATO sta su file: cosi' la pagina mostra sempre la realta' e un
    #  riavvio del controllore non lascia in giro una coda fantasma.
    #
    #  Il TOML viene salvato una volta sola all'avvio della coda e rimesso a
    #  posto alla fine, comunque vada. La stessa disciplina dello scan del
    #  V812, che e' in fondo una coda specializzata.

    def leggi_coda(self):
        try:
            with open(CODA) as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        d.setdefault("attiva", False)
        d.setdefault("voci", [])
        d.setdefault("backup_toml", None)
        d.setdefault("messaggio", "")
        return d

    def _scrivi_coda(self, d):
        try:
            with open(CODA, "w") as f:
                json.dump(d, f, indent=1)
        except OSError:
            pass

    def coda_attiva(self):
        return bool(self.leggi_coda().get("attiva"))

    def coda_aggiungi(self, nome, secondi, modifiche):
        with self.lock:
            d = self.leggi_coda()
            if d["attiva"]:
                return False, "The queue is running: stop it to change it."

            # Si validano adesso, non quando la run tocchera' a questa voce:
            # scoprire alle tre di notte che la quinta run della coda aveva un
            # valore illegale non e' una bella scoperta.
            spec = {(s, c): (e, ti, de) for s, c, e, ti, de in CAMPI}
            pulite = []
            for sezione, chiave, valore in modifiche:
                if (sezione, chiave) not in spec:
                    return False, "[%s] %s cannot be changed." % (sezione, chiave)
                etichetta, tipo, dettagli = spec[(sezione, chiave)]
                try:
                    pulite.append([sezione, chiave,
                                   _toml_da_ui(tipo, valore, dettagli, etichetta)])
                except ValueError as e:
                    return False, str(e)

            sec = None
            if str(secondi or "").strip():
                try:
                    sec = float(secondi)
                    if not 1 <= sec <= 86400:
                        raise ValueError
                except (TypeError, ValueError):
                    return False, "The duration must be between 1 and 86400 seconds."

            d["voci"].append({
                "id": max([v["id"] for v in d["voci"]] + [0]) + 1,
                "nome": (nome or "").strip()[:60] or "run %d" % (len(d["voci"]) + 1),
                "modifiche": pulite, "secondi": sec,
                "stato": "pending", "run": None, "eventi": None, "messaggio": "",
            })
            self._scrivi_coda(d)
            return True, "Added to the queue: %s" % d["voci"][-1]["nome"]

    def coda_rimuovi(self, voce_id):
        with self.lock:
            d = self.leggi_coda()
            if d["attiva"]:
                return False, "The queue is running: stop it to change it."
            prima = len(d["voci"])
            d["voci"] = [v for v in d["voci"] if str(v["id"]) != str(voce_id)]
            self._scrivi_coda(d)
            return (len(d["voci"]) < prima,
                    "Entry removed." if len(d["voci"]) < prima else "Entry not found.")

    def coda_riprova(self):
        """Rimette in attesa tutto quello che non e' andato a buon fine.

        Serve dopo un'interruzione: le voci interrotte o fallite restano li' a
        documentare cosa e' successo, ma senza questo non si potrebbero piu'
        eseguire se non cancellandole e riscrivendole."""
        with self.lock:
            d = self.leggi_coda()
            if d["attiva"]:
                return False, "La coda e' in esecuzione."
            n = 0
            for v in d["voci"]:
                if v["stato"] in ("interrupted", "failed"):
                    v.update(stato="pending", run=None, eventi=None, messaggio="")
                    n += 1
            self._scrivi_coda(d)
            return (n > 0, "%d entries put back in the queue." % n if n
                    else "There is nothing to retry.")

    def coda_svuota(self):
        with self.lock:
            d = self.leggi_coda()
            if d["attiva"]:
                return False, "The queue is running: stop it first."
            d["voci"] = []
            self._scrivi_coda(d)
            return True, "Queue cleared."

    def _applica(self, modifiche):
        """Scrive le modifiche di una voce. Senza backup: la coda ne ha gia'
        uno suo, preso all'avvio, e uno per voce riempirebbe la cartella."""
        try:
            testo = io.open(self.toml, encoding="utf-8").read()
        except OSError as e:
            return False, str(e)
        try:
            nuovo, _ = tomledit.sostituisci(testo, [tuple(m) for m in modifiche])
        except KeyError as e:
            return False, str(e).strip("\'")
        try:
            import tomllib
            errori, _ = coerenza(tomllib.loads(nuovo))
            if errori:
                return False, "; ".join(errori)
        except ImportError:
            pass
        except Exception as e:
            return False, "Invalid TOML: %s" % e
        try:
            io.open(self.toml, "w", encoding="utf-8").write(nuovo)
        except OSError as e:
            return False, str(e)
        return True, ""

    def coda_avvia(self):
        with self.lock:
            d = self.leggi_coda()
            if d["attiva"]:
                return False, "The queue is already running."
            if trova_scan():
                return False, "A scan is running."
            if trova_daq():
                return False, "A run is in progress: stop it first."
            da_fare = [v for v in d["voci"] if v["stato"] == "pending"]
            if not da_fare:
                return False, ("No pending entries. Add some, or use Retry to put "
                               "the interrupted ones back in the queue.")

            backup = "%s.bak-coda-%s" % (self.toml, time.strftime("%Y%m%d-%H%M%S"))
            try:
                io.open(backup, "w", encoding="utf-8").write(
                    io.open(self.toml, encoding="utf-8").read())
            except OSError as e:
                return False, "Cannot back up the TOML: %s" % e

            d["attiva"] = True
            d["backup_toml"] = backup
            d["messaggio"] = "running"
            self._scrivi_coda(d)

        threading.Thread(target=self._lavoratore, daemon=True).start()
        return True, "Queue started: %d runs to go." % len(da_fare)

    def _chiudi_coda(self, messaggio):
        """Disattiva la coda e rimette il TOML com'era. Da chiamare SEMPRE,
        qualunque sia il motivo per cui la coda finisce."""
        with self.lock:
            d = self.leggi_coda()
            d["attiva"] = False
            d["messaggio"] = messaggio
            # Una voce lasciata "in corso" direbbe il falso: quella run non e'
            # andata a termine, e lo stato deve dirlo.
            for v in d["voci"]:
                if v["stato"] == "running":
                    v["stato"] = "interrupted"
            b = d.get("backup_toml")
            if b and os.path.exists(b):
                try:
                    io.open(self.toml, "w", encoding="utf-8").write(
                        io.open(b, encoding="utf-8").read())
                except OSError:
                    d["messaggio"] += "  (WARNING: could not restore the TOML)"
            d["backup_toml"] = None
            self._scrivi_coda(d)

    def coda_ferma(self):
        d = self.leggi_coda()
        if not d["attiva"]:
            return False, "The queue is not running."
        # Prima si spegne la coda, poi si ferma la run: all'inverso
        # l'esecutore partirebbe con la voce successiva.
        with self.lock:
            d = self.leggi_coda()
            d["attiva"] = False
            self._scrivi_coda(d)
        if trova_daq():
            with self.lock:
                self._ferma(da_coda=True)
        self._chiudi_coda("Stopped by hand.")
        return True, "Queue stopped and TOML restored."

    def _lavoratore(self):
        """Esegue la coda, una voce per volta."""
        while True:
            d = self.leggi_coda()
            if not d.get("attiva"):
                return
            voce = next((v for v in d["voci"] if v["stato"] == "pending"), None)
            if voce is None:
                self._chiudi_coda("Queue completed.")
                return

            def segna(**campi):
                dd = self.leggi_coda()
                for v in dd["voci"]:
                    if v["id"] == voce["id"]:
                        v.update(campi)
                self._scrivi_coda(dd)

            segna(stato="running", messaggio="")
            ok, errore = self._applica(voce["modifiche"])
            if not ok:
                segna(stato="failed", messaggio=errore)
                continue

            with self.lock:
                avviata, messaggio = self._avvia(da_coda=True)
            if not avviata:
                segna(stato="failed", messaggio=messaggio)
                continue

            inizio = time.time()
            while True:
                time.sleep(0.5)
                if not trova_daq():
                    break
                if not self.leggi_coda().get("attiva"):
                    return                      # ferma_coda ha gia' fatto tutto
                if voce["secondi"] and time.time() - inizio >= voce["secondi"]:
                    with self.lock:
                        self._ferma(da_coda=True)
                    break

            ev, _, runfile = self.eventi_correnti()
            segna(stato="done", run=os.path.basename(runfile) if runfile else None,
                  eventi=ev, messaggio="%.0f s" % (time.time() - inizio))

    # -- scan --------------------------------------------------------------
    def coda_scan(self, n=30):
        try:
            with open(SCAN_LOG, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 40000))
                return righe_terminale(f.read().decode("utf-8", "replace"))[-n:]
        except OSError:
            return []

    # "[punto 3/6] offset 5": lo stampano tutti e due gli scan prima di
    # cominciare un punto.
    # Gli spazi intorno alla barra sono tollerati apposta: il marcatore lo
    # scrivono due programmi diversi, uno in python e uno in bash, e un
    # avanzamento che sparisce per uno spazio sarebbe una trappola sciocca.
    PUNTO = re.compile(r"\[punto\s*(\d+)\s*/\s*(\d+)\s*\]\s*(.*)")

    def avanzamento_scan(self):
        """(fatti, totale, descrizione) dell'ultimo punto cominciato, o None."""
        for riga in reversed(self.coda_scan(60)):
            m = self.PUNTO.search(riga)
            if m:
                return [int(m.group(1)), int(m.group(2)), m.group(3).strip()]
        return None

    def avvia_scan(self, tipo, valori, secondi):
        """Lancia uno scan. I due hanno prerequisiti OPPOSTI, e la pagina deve
        dirlo chiaro invece di limitarsi a fallire: quello del V1742 cambia le
        soglie a caldo e vuole una run gia' in corso, quello del V812 fa una
        run per punto e vuole la DAQ ferma."""
        with self.lock:
            if tipo not in SCAN:
                return False, "Unknown scan: %s" % tipo
            if trova_scan():
                return False, "A scan is already running."
            if self.coda_attiva():
                return False, "A queue is running: it is the one driving the DAQ."
            spec = SCAN[tipo]

            in_corso = trova_daq() is not None
            if spec["serve_run"] and not in_corso:
                return False, ("The self-trigger scan changes thresholds on a running "
                               "acquisition: start the run first.")
            if not spec["serve_run"] and in_corso:
                return False, ("The CFD scan makes one run per point: stop the current "
                               "one first.")

            # I valori finiscono in argv, mai in una shell, ma si controllano
            # lo stesso: un carattere strano qui sarebbe un refuso, non un
            # attacco, e vale la pena dirlo subito invece di farlo scoprire
            # allo script.
            pezzi = [x for x in str(valori).replace(",", " ").split() if x]
            if not pezzi:
                return False, "You did not give any value to try."
            for x in pezzi:
                try:
                    float(x)
                except ValueError:
                    return False, "'%s' is not a number." % x
            try:
                sec = float(secondi)
                if not 1 <= sec <= 3600:
                    raise ValueError
            except (TypeError, ValueError):
                return False, "Seconds per point must be between 1 and 3600."

            try:
                log = open(SCAN_LOG, "wb")
            except OSError as e:
                return False, "Cannot write %s: %s" % (SCAN_LOG, e)
            try:
                proc = subprocess.Popen(
                    ["bash", spec["script"], spec["opzione"], " ".join(pezzi),
                     "-s", str(int(sec))],
                    cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, start_new_session=True)
            except OSError as e:
                return False, "Start failed: %s" % e
            finally:
                log.close()

            stato = {"pid": proc.pid, "pgid": proc.pid, "tipo": tipo,
                     "script": spec["script"], "valori": " ".join(pezzi),
                     "secondi": sec, "avviato": time.time()}
            try:
                with open(SCAN_STATO, "w") as f:
                    json.dump(stato, f)
            except OSError:
                pass

            # Uno scan che si rifiuta di partire muore in meno di un secondo:
            # i controlli preliminari degli script -- "serve una run in
            # corso", "live-status.json e' vecchio", "gira con SelfTrigger =
            # false" -- stampano e escono. Senza questa attesa la pagina
            # diceva "scan started", la riga SCAN RUNNING lampeggiava per un
            # giro e spariva, e il motivo restava solo dentro un file di log
            # che nessuno guarda. E' successo: sei avvii di fila, tutti
            # registrati come riusciti, nessuno partito davvero.
            for _ in range(20):
                time.sleep(0.1)
                if proc.poll() is not None:
                    break
            if proc.poll() is not None:
                righe = [r for r in self.coda_scan(12) if r.strip()]
                perche = " ".join(righe[-3:]) if righe else \
                    "no output: look at data/scan-console.log"
                try:
                    os.unlink(SCAN_STATO)
                except OSError:
                    pass
                return False, "The scan stopped at once. " + perche

            return True, "%s scan started on %d points." % (spec["etichetta"], len(pezzi))

    def ferma_scan(self):
        """SIGTERM al GRUPPO, mai KILL.

        Il gruppo perche' lo scan ha figli -- la DAQ di ogni punto -- e vanno
        fermati anche loro. SIGTERM perche' e' quello che fa scattare il trap
        dello script, che rimette a posto il TOML, e che fa chiudere la DAQ
        per la porta buona. Un kill -9 lascerebbe il tuo file con la soglia
        dell'ultimo punto e un HDF5 a meta'.
        """
        with self.lock:
            s = trova_scan()
            if not s:
                return False, "No scan is running."
            try:
                os.killpg(s["pgid"], signal.SIGTERM)
            except OSError as e:
                return False, "Signal failed: %s" % e
            fermo = None
            for i in range(80):
                time.sleep(0.5)
                if not trova_scan():
                    fermo = (i + 1) * 0.5
                    break
            if fermo is None:
                return False, "The scan has not responded for 40 s."

            # Lo script muore prima della DAQ che aveva lanciato: quella sta
            # chiudendo il file e comprimendolo, e ci mette il suo. Tornare
            # qui senza aspettarla farebbe trovare alla prossima azione una
            # DAQ ancora viva, con un messaggio incomprensibile.
            for j in range(60):
                if not trova_daq():
                    return True, ("Scan stopped in %.1f s. The TOML was restored and the "
                                  "run of the last point was closed "
                                  "properly." % fermo)
                time.sleep(0.5)
            return True, ("Scan stopped in %.1f s and TOML restored, but the run of the "
                          "last point is still closing: wait a few seconds before "
                          "starting another." % fermo)

    # -- azioni ------------------------------------------------------------
    def avvia(self, da_coda=False):
        with self.lock:
            return self._avvia(da_coda)

    def _con_hv(self, esito, messaggio):
        # La tensione dei SiPM accanto a ogni avvio e arresto, nel registro
        # delle azioni: il CSV la ha comunque, ma qui la si legge senza
        # andarla a cercare.
        if esito and self.psu:
            messaggio += "  [%s]" % self.psu.riassunto()
        return esito, messaggio

    def _avvia(self, da_coda=False):
        if not da_coda and self.coda_attiva():
            return False, "A queue is running: it is the one driving the DAQ."
        if trova_scan():
            return False, "A scan is running: it is the one driving the DAQ."
        if trova_daq():
            return False, "A DAQ is already running."
        if not os.path.exists(BINARIO):
            return False, "Binary not found: %s" % BINARIO
        if not os.path.exists(self.toml):
            return False, "Configuration not found: %s" % self.toml
        try:
            log = open(self.log_path, "wb")
        except OSError as e:
            return False, "Cannot write %s: %s" % (self.log_path, e)
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
            return False, "Start failed: %s" % e
        finally:
            log.close()
        for _ in range(50):
            time.sleep(0.1)
            if trova_daq():
                return True, "DAQ started."
        return False, "Started but I cannot find it among the processes: check the log."

    def cartella_uscita(self):
        """OutputDir dal TOML, oppure None se non si riesce a leggerlo."""
        try:
            import tomllib
            with open(self.toml, "rb") as f:
                d = tomllib.load(f)
            v = d.get("digitizer", {}).get("OutputDir")
            return str(v) if v else None
        except Exception:
            return None

    def avvia_monitor(self):
        """Accende il monitor sulla macchina DAQ.

        La pagina offriva un link e basta, e un link apre una pagina: se il
        processo non c'era si finiva su un errore del browser, senza sapere se
        mancasse il monitor, la rete o l'inoltro della porta. Potendolo
        accendere da qui la domanda non si pone piu'.
        """
        with self.lock:
            if trova_monitor():
                return False, "The monitor is already running."
            if not os.path.exists(MONITOR):
                return False, "Not found: %s" % MONITOR
            try:
                log = open(os.path.join(ROOT, "data", "monitor-console.log"), "wb")
            except OSError as e:
                return False, "Cannot write the monitor log: %s" % e
            # Il monitor deve guardare DOVE scrive la DAQ. Da quando la
            # cartella si cambia dalla pagina, l'alternativa era un monitor
            # che continua a mostrare la cartella vecchia -- cioe' dati di
            # un'altra sessione presentati come se fossero di adesso, che e'
            # esattamente l'equivoco che si vuole togliere di mezzo.
            args = list(self.args_monitor)
            if not any(a in ("-d", "--data-dir") for a in args):
                cartella = self.cartella_uscita()
                if cartella:
                    args += ["-d", cartella]

            try:
                # start_new_session come per la DAQ: il monitor deve
                # sopravvivere al riavvio del controllore, se no riavviare
                # questa pagina spegnerebbe la vista sulla run in corso.
                subprocess.Popen([sys.executable, MONITOR] + args,
                                 cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True)
            except OSError as e:
                return False, "Start failed: %s" % e
            finally:
                log.close()
            # Si aspetta la PORTA, non il processo: e' quella che serve a chi
            # clicca il link, e un processo che parte e muore subito (porta
            # occupata, file dati assente) risulterebbe comunque "avviato".
            for _ in range(60):
                time.sleep(0.1)
                global _monitor_visto
                _monitor_visto = (0.0, False)
                if monitor_acceso(self.porta_monitor):
                    return True, "Monitor started."
            return False, ("Started but it is not answering on port %d: "
                           "look at data/monitor-console.log." % self.porta_monitor)

    def ferma_monitor(self):
        with self.lock:
            pid = trova_monitor()
            if not pid:
                return False, "The monitor is not running."
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError as e:
                return False, "Cannot stop it: %s" % e
            for _ in range(ATTESA_ARRESTO_S * 2):
                time.sleep(0.5)
                if not trova_monitor():
                    global _monitor_visto
                    _monitor_visto = (0.0, False)
                    return True, "Monitor stopped."
            return False, "It is not closing: pid %d is still there." % pid

    def ferma(self, da_coda=False):
        with self.lock:
            return self._ferma(da_coda)

    def _ferma(self, da_coda=False):
        if not da_coda and self.coda_attiva():
            return False, ("A queue is running: stopping the single run would leave "
                           "it half done. Use Stop queue.")
        if trova_scan():
            return False, ("A scan is running: stopping the single run would leave "
                           "it half done. Use Stop scan.")
        pid = trova_daq()
        if not pid:
            return False, "No DAQ is running."
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as e:
            return False, "Signal failed: %s" % e
        for i in range(ATTESA_ARRESTO_S * 2):
            time.sleep(0.5)
            if not trova_daq():
                return True, "Run closed in %.1f s." % ((i + 1) * 0.5)
        return False, ("Still alive after %d s. The link is probably hung: "
                       "a second stop terminates it at once."
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
<title>DAQ Control</title>
<style>
 body{font:14px/1.45 system-ui,sans-serif;margin:0;padding:12px;background:#f6f6f4;color:#1a1a19}
 h1{font-size:18px;margin:0 0 14px}
 .riga{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start}
 .box{background:#fff;border:1px solid #e2e2de;border-radius:8px;padding:10px 12px;flex:1 1 320px}
 /* Colonna di riquadri impilati dentro una .riga: serve per mettere i pannelli
    bassi di fianco a Configuration, che e' alto e lasciava un vuoto. */
 .colonna{display:flex;flex-direction:column;gap:12px;flex:1 1 330px;min-width:0}
 .colonna>.box{flex:0 0 auto}
 /* Le spiegazioni lunghe stanno dietro un "?": servono a chi arriva nuovo,
    non ogni giorno, e in mezzo ai piedi costano quattro righe a testa. */
 .qm{display:inline-block;width:15px;height:15px;line-height:15px;text-align:center;
     border:1px solid #d5d5d0;border-radius:50%;font-size:10px;color:#6b6a65;
     cursor:pointer;margin-left:6px;vertical-align:1px;user-select:none}
 .qm:hover{background:#ececea}
 .aiuto{display:none;font-size:12px;color:#6b6a65;margin-top:8px;
        border-left:2px solid #e2e2de;padding-left:8px}
 .aiuto.apri{display:block}
 /* Richiudere un pannello: si nasconde tutto tranne il titolo, che resta
    cliccabile. Il triangolo e il riepilogo stanno nel titolo, cosi' chiuso il
    pannello dice ancora cosa contiene invece di diventare una riga muta. */
 .box>h2{cursor:pointer;user-select:none}
 .box>h2::before{content:"\25be\00a0\00a0";color:#b5b5b0;font-size:11px}
 .box.chiuso>h2::before{content:"\25b8\00a0\00a0"}
 .box.chiuso>*:not(h2){display:none}
 .riass{float:right;font-weight:400;text-transform:none;letter-spacing:0;
        color:#9a9a94;font-size:11px}
 /* Le due tabelle per canale: 32 righe il V1742 e 16 il V812, sono la cosa
    piu' alta della pagina e quasi sempre non si guardano. */
 .sezh{cursor:pointer;user-select:none;font-size:12px;text-transform:uppercase;
       letter-spacing:.04em;color:#6b6a65;margin-bottom:4px}
 .sezh::before{content:"\25be\00a0\00a0";color:#b5b5b0;font-size:11px}
 .sez.chiuso .sezh::before{content:"\25b8\00a0\00a0"}
 .sez.chiuso .sezc{display:none}
 .sezr{text-transform:none;letter-spacing:0;color:#9a9a94;margin-left:8px}
 .box h2{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:#6b6a65;margin:0 0 6px}
 .stato{display:inline-block;padding:4px 12px;border-radius:999px;font-weight:600}
 .ferma{background:#ececea;color:#52514e}
 .corso{background:#dcefe4;color:#15603a}
 button{font:inherit;padding:6px 14px;border-radius:6px;border:1px solid transparent;cursor:pointer}
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
     max-height:170px;font-size:12px;margin:0;white-space:pre-wrap}
 .msg{padding:9px 12px;border-radius:6px;margin:10px 0;display:none}
 .ok{background:#dcefe4;color:#15603a}
 .ko{background:#f8e0da;color:#8a2a18}
 .az{font-size:12px;color:#52514e}
 .hvnum{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums}
 .hvu{font-size:13px;font-weight:400;color:#6b6a65;margin-left:3px}
 .hvc{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:8px}
 .hvc input{width:70px}
 .hvc .k{font-size:12px;color:#6b6a65;min-width:70px}
 .hvg{display:block;width:100%;height:62px;cursor:crosshair}
 .hvf button{padding:2px 8px;font-size:11px;background:#ececea}
 .hvf button.sel{background:#1a1a19;color:#fff}
 input{font:inherit;padding:6px 9px;border:1px solid #d5d5d0;border-radius:6px}
 a{color:#2a78d6}
</style></head><body>
<h1>DAQ Control</h1>

<div class="box" style="margin-bottom:14px">
  <span id="badge" class="stato ferma">...</span>
  <span id="sommario" style="margin-left:14px;color:#52514e"></span>
  <div style="margin-top:14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
    <button id="avvia">Start run</button>
    <button id="ferma">Stop run</button>
    <!-- Qui in alto e non in fondo a Configuration: l'avviso delle modifiche
         non salvate deve vedersi accanto a Start run, che e' dove si scopre
         troppo tardi di aver lanciato la run col file vecchio. -->
    <button id="salva" style="background:#2a78d6;color:#fff">Save to TOML</button>
    <span id="nonsalvato" style="display:none;color:#a8321f;font-weight:600;font-size:12px">
      &#9679; unsaved changes</span>
    <button id="monavvia">Start monitor</button>
    <button id="monferma">Stop monitor</button>
    <a id="mon" href="#" target="_blank" rel="noopener"
       style="margin-left:6px;font-size:13px">Open monitor</a>
    <a id="mondir" href="#" target="_blank" rel="noopener"
       style="font-size:12px;color:#6b6a65"
       title="Straight to the monitor port: only works from a network that can reach it">direct</a>
    <span id="monstato" style="font-size:12px;color:#6b6a65"></span>
    <label style="margin-left:auto;color:#6b6a65">who are you
      <input id="chi" placeholder="your name" style="width:140px">
    </label>
  </div>
  <div id="msg" class="msg"></div>
</div>

<div class="riga">
  <div class="box" id="b_cfg" style="flex:2 1 560px"><h2>Configuration<span class="riass"></span></h2>
    <div id="cfgfile" style="font-size:12px;color:#6b6a65;margin-bottom:8px"></div>
    <div id="cfg"></div>
    <div id="tabdig" style="margin-top:14px"></div>
    <div id="tabcfd" style="margin-top:14px"></div>
    <div style="margin-top:12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <button id="ricarica" style="background:#ececea">Reload file</button>
      <span style="font-size:12px;color:#6b6a65">
        applies from the next run, except entries marked <b>live</b>
      </span>
    </div>
    <div style="margin-top:12px;padding-top:10px;border-top:1px solid #eee">
      <label style="font-size:12px;color:#6b6a65">self-trigger thresholds on the RUNNING acquisition
        <input id="caldo" placeholder="e.g. 5  or  4 6" style="width:110px">
      </label>
      <button id="applica" style="background:#ececea;margin-left:6px">Apply now</button>
    </div>
  </div>
  <div class="colonna">
  <div class="box" id="b_run"><h2>Current run<span class="riass"></span></h2><table id="run"></table></div>

<div class="box" id="b_psu"><h2>SiPM bias supply<span class="qm" data-aiuto="aiutopsu">?</span><span class="riass"></span></h2>
  <div id="psuerr" class="msg ko" style="margin:0 0 8px"></div>
  <div style="display:flex;gap:18px;align-items:baseline;flex-wrap:wrap">
    <span id="psuout" class="stato ferma">&hellip;</span>
    <span><span id="psuv" class="hvnum">&mdash;</span><span class="hvu">V</span></span>
    <span><span id="psui" class="hvnum">&mdash;</span><span class="hvu">&micro;A</span></span>
    <span id="psurampa" style="font-size:12px;color:#2a78d6"></span>
  </div>
  <div id="psuinfo" style="font-size:12px;color:#6b6a65;margin-top:4px"></div>
  <div class="hvc">
    <span class="k">voltage</span>
    <input id="psuvset" placeholder="V"><button id="psuvgo" style="background:#2a78d6;color:#fff">Ramp to</button>
    <span style="margin-left:auto;display:flex;gap:6px">
      <button id="psuon" style="background:#15603a;color:#fff">Output ON</button>
      <button id="psuoff" style="background:#a8321f;color:#fff">Output OFF</button>
    </span>
  </div>
  <div class="hvc">
    <span class="k">current limit</span>
    <input id="psuilim" placeholder="&micro;A"><button id="psuigo" style="background:#ececea">Set</button>
    <span class="k" style="margin-left:12px;min-width:0">OVP</span>
    <input id="psuovp" placeholder="V"><button id="psuogo" style="background:#ececea">Set</button>
    <button id="psufind" style="background:#ececea;display:none">Find supply</button>
  </div>
  <div id="aiutopsu" class="aiuto">
    Aim-TTi PLH250-P biasing the SiPMs. The voltage changes only when someone presses
    <b>Ramp to</b>: it moves in 5 V steps every 0.5 s and is refused above the limit
    shown. <b>Output OFF</b> works at any time, also in the middle of a ramp. Every
    reading is written to <code>data/psu-log.csv</code>, run or no run, and the bias
    is noted in Recent actions at every start and stop of a run. The front panel
    stays usable: the controller hands it back after each reading.
  </div>
  <div style="display:flex;align-items:center;gap:8px;margin-top:10px">
    <span style="font-size:12px;color:#6b6a65">stability</span>
    <span class="hvf" id="psufin">
      <button data-ore="1">1 h</button><button data-ore="6">6 h</button><button data-ore="24">24 h</button>
    </span>
    <span id="psuhover" style="margin-left:auto;font-size:12px;color:#52514e;font-variant-numeric:tabular-nums"></span>
  </div>
  <div style="font-size:11px;color:#6b6a65;margin-top:4px">output voltage [V]</div>
  <canvas id="psugv" class="hvg"></canvas>
  <div style="font-size:11px;color:#6b6a65;margin-top:2px">output current [&micro;A]</div>
  <canvas id="psugi" class="hvg"></canvas>
</div>

<div class="box" id="b_coda"><h2>Run queue<span class="qm" data-aiuto="aiutocoda">?</span><span class="riass"></span></h2>
  <div id="codastato" style="margin-bottom:10px"></div>
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
    <input id="cnome" placeholder="run name" style="width:190px">
    <input id="csec" placeholder="duration [s]" style="width:95px" title="empty = stops at NEvents">
    <button id="cadd" style="background:#ececea">Add current configuration</button>
    <span style="margin-left:auto;display:flex;gap:8px">
      <button id="cgo" style="background:#15603a;color:#fff">Start queue</button>
      <button id="cstop" style="background:#a8321f;color:#fff">Stop queue</button>
      <button id="cretry" style="background:#ececea">Retry unfinished</button>
      <button id="cclr" style="background:#ececea">Clear</button>
    </span>
  </div>
  <div id="aiutocoda" class="aiuto">
    An entry is the difference between what you have in the form now and what is in
    the file: set the parameters, give it a name, add it. Then change them and add
    another. The TOML is saved when the queue starts and restored when it ends.
  </div>
  <div id="codatab" style="margin-top:10px"></div>
</div>

<div class="box" id="b_scan"><h2>Threshold scan<span class="riass"></span></h2>
  <div id="scanstato" style="margin-bottom:10px"></div>
  <div style="display:flex;gap:22px;flex-wrap:wrap">
    <div>
      <div style="font-size:12px;color:#6b6a65;margin-bottom:4px">V1742 self-trigger &mdash; offsets</div>
      <input id="s1val" value="3 4 5 6 8 10" style="width:170px">
      <input id="s1sec" value="20" style="width:52px" title="seconds per point">
      <button id="s1go" style="background:#2a78d6;color:#fff">Start</button>
      <div style="font-size:11px;color:#6b6a65;margin-top:3px">changes thresholds live: needs a run already in progress</div>
    </div>
    <div>
      <div style="font-size:12px;color:#6b6a65;margin-bottom:4px">V812 CFD &mdash; thresholds [mV]</div>
      <input id="s2val" value="5 7 10 15 20 30" style="width:170px">
      <input id="s2sec" value="60" style="width:52px" title="seconds per point">
      <button id="s2go" style="background:#eb6834;color:#fff">Start</button>
      <div style="font-size:11px;color:#6b6a65;margin-top:3px">one run per point: the DAQ must be stopped</div>
    </div>
    <div style="margin-left:auto;align-self:flex-end">
      <button id="sstop" style="background:#a8321f;color:#fff">Stop scan</button>
    </div>
  </div>
  <pre id="scanlog" style="margin-top:12px;display:none"></pre>
</div>

<div class="box" id="b_log"><h2>DAQ log</h2><pre id="log"></pre></div>
  </div>
</div>

<div class="box" id="b_plot" style="margin-top:12px"><h2>Scan plots<span class="qm" data-aiuto="aiutoplot">?</span><span class="riass"></span></h2>
  <div style="display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap">
    <select id="gsel" multiple size="5"
            style="font:inherit;padding:4px 8px;border:1px solid #d5d5d0;border-radius:6px;min-width:400px"></select>
    <div style="display:flex;flex-direction:column;gap:6px">
      <label style="font-size:12px;color:#52514e">
        <input type="checkbox" id="glog"> log scale
      </label>
      <label style="font-size:12px;color:#52514e"
             title="counts each channel over threshold, recomputed from the recorded waveforms">
        <input type="checkbox" id="gperch"> per channel
      </label>
      <input id="gchan" placeholder="all channels" style="width:98px;font-size:12px"
             title="which channels to draw, e.g. 8,9 - empty means every channel in the file, including the unconnected ones">
      <input id="gmvoff" placeholder="mV/offset" style="width:98px;font-size:12px"
             title="mV per offset unit, to convert the threshold. Empty = the measured value for that sampling rate (2.5 GS/s and 1 GS/s only). At other rates the per-channel plot needs a number here, and the plot will say it is assumed.">
      <button id="gdraw" style="background:#2a78d6;color:#fff">Draw</button>
      <button id="ggo" style="background:#ececea">Refresh list</button>
      <a id="gapri" href="#" target="_blank" style="font-size:12px">open full size</a>
    </div>
  </div>
  <div id="aiutoplot" class="aiuto">
    Pick one or more measurements (ctrl-click) and draw them together. Up to 8:
    beyond that the curves stop being distinguishable. <b>per channel</b> puts the
    threshold in millivolts, so it needs the mV per offset unit: that is measured
    at 2.5 GS/s and 1 GS/s only, and at any other rate you must type it in the
    <b>mV/offset</b> box &mdash; the plot then labels the axis ASSUMED. With <b>per channel</b>,
    leaving the channel box empty draws every channel in the file &mdash; including
    the ones with nothing plugged in, which sit flat near zero.
  </div>
  <div id="gvuoto" style="color:#6b6a65;font-size:12px;margin-top:8px"></div>
  <img id="gimg" style="margin-top:10px;max-width:100%;border:1px solid #e2e2de;border-radius:6px;display:none">
</div>

<div class="box" id="b_azioni" style="margin-top:12px"><h2>Recent actions<span class="riass"></span></h2><div id="azioni" class="az"></div></div>
<p style="color:#6b6a65;font-size:12px">Plots and DQM are in the monitor, linked at the top of this page.</p>

<script>
const $ = id => document.getElementById(id);
$("chi").value = localStorage.getItem("chi") || "";
$("chi").oninput = () => localStorage.setItem("chi", $("chi").value);
// Il nome host e' quello da cui arriva QUESTA pagina, non un indirizzo
// scritto nel codice: da VPN, da collegamento diretto o da localhost il
// monitor sta sempre sulla stessa macchina del controllore, e cosi' il link
// resta giusto ovunque lo si apra. La porta la dichiara il server.
let PORTA_MON = 8765;
function aggiornaMonitor(s){
  PORTA_MON = s.monitor_porta || PORTA_MON;
  // Il link passa da QUESTA porta, non da hostname:8765: la pagina di
  // controllo spesso si guarda attraverso un inoltro di porta o una VPN dove
  // la porta del monitor non e' raggiungibile, e un link che funziona solo
  // dalla rete del laboratorio e' peggio di nessun link. Il collegamento
  // diretto resta accanto per chi sta in laboratorio e lo preferisce.
  $("mon").href = "/monitor/" + (TOKEN ? "?token=" + encodeURIComponent(TOKEN) : "");
  $("mondir").href = location.protocol + "//" + location.hostname + ":" + PORTA_MON + "/";
  const acceso = !!s.monitor;

  // Spento, il link non viene nascosto ma disattivato: sparire e ricomparire
  // sotto il puntatore e' peggio che restare li' spiegando perche' non si
  // puo' cliccare.
  $("mon").style.pointerEvents = acceso ? "" : "none";
  $("mon").style.color = acceso ? "" : "#b5b5b0";
  $("monavvia").style.display = acceso ? "none" : "";
  $("monferma").style.display = acceso ? "" : "none";
  $("mondir").style.display = acceso ? "" : "none";
  $("monstato").textContent = acceso
    ? "on port " + PORTA_MON
    : "not running";
  $("mon").title = acceso
    ? "Opens the monitor in a new tab"
    : "The monitor is not running: press Start monitor";
}
$("mon").href = location.protocol + "//" + location.hostname + ":" + PORTA_MON + "/";

const PAR = new URLSearchParams(location.search);
const TOKEN = PAR.get("token") || "";

// Gli errori NON spariscono da soli. Un rifiuto che svanisce dopo otto
// secondi lascia la pagina con le modifiche ancora visibili e il file
// invariato: si lancia la run convinti di aver salvato. E' successo, con un
// salvataggio rifiutato per conflitto che nessuno ha visto passare.
function msg(testo, ok){
  const m = $("msg");
  m.textContent = testo + (ok ? "" : "          (clic per chiudere)");
  m.className = "msg " + (ok ? "ok" : "ko");
  m.style.display = "block";
  m.style.cursor = ok ? "default" : "pointer";
  m.onclick = ok ? null : () => { m.style.display = "none"; };
  if(window._msgT) clearTimeout(window._msgT);
  if(ok) window._msgT = setTimeout(() => { m.style.display = "none"; }, 8000);
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
  }catch(e){ msg("Request failed: " + e, false); }
  aggiorna();
}

// Il monitor non chiede conferma: accenderlo e spegnerlo non tocca i dati
// ne' la run, ed e' un gesto che si fa spesso.
async function azioneMonitor(che){
  $("monavvia").disabled = $("monferma").disabled = true;
  try{
    const q = new URLSearchParams({chi: $("chi").value, token: TOKEN, azione: che});
    const d = await (await fetch("/api/monitor?" + q, {method: "POST"})).json();
    msg(d.messaggio, d.esito);
  }catch(e){ msg("Request failed: " + e, false); }
  $("monavvia").disabled = $("monferma").disabled = false;
  aggiorna();
}
$("monavvia").onclick = () => azioneMonitor("avvia");
$("monferma").onclick = () => azioneMonitor("ferma");

// Un clic sul titolo chiude il pannello. Lo stato resta fra un reload e
// l'altro: chi lavora con la coda chiusa non deve richiuderla ogni volta.
document.querySelectorAll(".box[id] > h2").forEach(h => {
  const box = h.parentElement;
  try { if(localStorage.getItem("box." + box.id) === "1") box.classList.add("chiuso"); }
  catch(e){}
  h.onclick = ev => {
    // il "?" delle spiegazioni sta dentro al titolo: cliccarlo non deve
    // richiudere anche il pannello
    if(ev.target.classList.contains("qm")) return;
    box.classList.toggle("chiuso");
    try { localStorage.setItem("box." + box.id,
                               box.classList.contains("chiuso") ? "1" : "0"); } catch(e){}
  };
});

function riass(id, testo){
  const e = $(id) && $(id).querySelector(".riass");
  if(e) e.textContent = testo || "";
}

// Le spiegazioni lunghe si aprono col "?" e lo stato resta fra un reload e
// l'altro: chi le vuole aperte non deve riaprirle ogni volta, chi non le vuole
// non se le ritrova.
document.querySelectorAll(".qm").forEach(q => {
  const id = q.dataset.aiuto;
  const box = $(id);
  try { if(localStorage.getItem("aiuto." + id) === "1") box.classList.add("apri"); }
  catch(e){}
  q.onclick = () => {
    box.classList.toggle("apri");
    try { localStorage.setItem("aiuto." + id, box.classList.contains("apri") ? "1" : "0"); }
    catch(e){}
  };
});

$("avvia").onclick = () => azione("avvia", "Start a new run?");
$("ferma").onclick = () => azione("ferma",
  "Stop the running acquisition?\n\nThe DAQ closes the file and resets the board: " +
  "nothing already acquired is lost.");

async function aggiorna(){
  let s;
  try{ s = await (await fetch("/api/stato?token=" + TOKEN)).json(); }
  catch(e){ $("badge").textContent = "service unreachable"; return; }

  $("badge").textContent = s.in_corso ? "RUNNING" : "stopped";
  $("badge").className = "stato " + (s.in_corso ? "corso" : "ferma");
  $("sommario").textContent = s.in_corso
      ? (s.run || "") + (s.rate !== null && s.rate !== undefined ? "   " + s.rate + " Hz" : "")
      : "";
  $("avvia").disabled = s.in_corso || !!s.scan || (s.coda && s.coda.attiva);
  $("ferma").disabled = !s.in_corso || !!s.scan || (s.coda && s.coda.attiva);
  aggiornaMonitor(s);
  aggiornaPsu(s.psu);

  // Riepiloghi nei titoli: chiuso, il pannello dice ancora la cosa per cui lo
  // si sarebbe aperto.
  riass("b_run", s.in_corso
          ? (s.run || "") + (s.rate != null ? "  " + s.rate + " Hz" : "")
          : "no run");
  const nc = ((s.coda && s.coda.voci) || []).length;
  riass("b_coda", (s.coda && s.coda.attiva) ? "running" :
                  (nc ? nc + (nc === 1 ? " entry" : " entries") : "empty"));
  // s.scan e non sc: const sc e' dichiarato piu' sotto, e leggerlo qui
  // sarebbe un ReferenceError che spegne tutto l'aggiornamento della pagina.
  riass("b_scan", s.scan
          ? (s.scan.avanzamento
               ? "point " + s.scan.avanzamento[0] + " of " + s.scan.avanzamento[1]
               : "running")
          : "idle");
  riass("b_azioni", (s.azioni && s.azioni.length)
          ? s.azioni[s.azioni.length - 1].quando : "");

  tabella($("run"), s.in_corso
    ? [["pid", s.pid],
       ["file", s.run],
       ["events", s.eventi === null ? "—" : s.eventi + " / " + s.eventi_richiesti],
       ["rate (last 30 s)", s.rate === null ? "waiting" : s.rate + " Hz"],
       ["running for", s.da_secondi === null ? "—" : Math.round(s.da_secondi) + " s"]]
    : [["", "no run in progress"]]);

  const cd = s.coda || {};
  const attesa = (cd.voci || []).filter(x => x.stato === "pending").length;
  $("codastato").innerHTML = cd.attiva
    ? `<span class="stato corso">QUEUE RUNNING</span>
       <span style="margin-left:12px;color:#52514e">${attesa} runs still to do</span>`
    : `<span style="color:#6b6a65">queue stopped${cd.messaggio ? " &mdash; " + cd.messaggio : ""}</span>`;
  $("cgo").disabled = cd.attiva || !attesa;
  $("cstop").disabled = !cd.attiva;
  $("cadd").disabled = cd.attiva;
  $("cclr").disabled = cd.attiva;
  $("cretry").disabled = cd.attiva ||
    !(cd.voci || []).some(x => x.stato === "interrupted" || x.stato === "failed");
  disegnaCoda(cd);

  const sc = s.scan;
  // Appena uno scan finisce compare il suo grafico, senza doverlo chiedere:
  // e' il momento in cui lo si vuole guardare.
  if(window._scanPrima && !sc) caricaGrafici().then(disegna);
  window._scanPrima = !!sc;
  // L'avanzamento e' la risposta a "sta andando?": senza, fra un punto e
  // l'altro passano venti secondi in cui la pagina dice solo "SCAN RUNNING" e
  // non si distingue uno scan che lavora da uno piantato.
  let avz = "";
  if(sc && sc.avanzamento){
    const [fatti, tot, che] = sc.avanzamento;
    const resta = Math.max(0, (tot - fatti + 1) * sc.secondi);
    avz = `<b>point ${fatti} of ${tot}</b> (${che}) &middot;
           ~${Math.round(resta)} s left &middot; `;
  }
  $("scanstato").innerHTML = sc
    ? `<span class="stato corso">SCAN RUNNING</span>
       <span style="margin-left:12px;color:#52514e">${avz}${sc.etichetta} &middot;
       points: ${sc.valori} &middot; ${sc.secondi} s each &middot;
       for ${Math.round(sc.da_secondi)} s</span>`
    : '<span style="color:#6b6a65">no scan running</span>';
  $("s1go").disabled = !!sc || !s.in_corso;
  $("s2go").disabled = !!sc || s.in_corso;
  $("sstop").disabled = !sc;
  // Il log resta visibile anche a scan finito: e' li' che si legge perche'
  // uno scan si e' rifiutato di partire, e nasconderlo appena il processo
  // muore vuol dire nasconderlo proprio quando serve.
  const righeScan = (sc && sc.log) || s.scan_log || [];
  $("scanlog").style.display = righeScan.length ? "block" : "none";
  $("scanlog").textContent = righeScan.join("\n");

  $("log").textContent = (s.log || []).join("\n");
  $("azioni").innerHTML = (s.azioni || []).slice().reverse().map(a =>
    `${a.quando} &middot; <b>${a.chi}</b> from ${a.da}: ${a.azione} &rarr; ${a.esito}`
  ).join("<br>") || "no actions recorded";
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

function tabellaCanali(dest, titolo, n, colonne, iniziale, riassunto, chiusaSeIgnota){
  // Richiudibile, con il riepilogo nel titolo: sono 32 righe per il V1742 e 16
  // per il V812, la cosa piu' alta della pagina, e aperte servono solo quando
  // si cambia il cablaggio. Chiusa, la riga di riepilogo dice comunque quali
  // canali sono accesi, che e' il motivo per cui la si aprirebbe.
  let chiusa = chiusaSeIgnota;
  try {
    const v = localStorage.getItem("sez." + dest);
    if(v !== null) chiusa = (v === "1");
  } catch(e){}

  let h = `<div class="sez${chiusa ? " chiuso" : ""}" id="sez_${dest}">
           <div class="sezh">${titolo}<span class="sezr">${riassunto || ""}</span></div>
           <div class="sezc"><div class="cantab"><table>
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
  $(dest).innerHTML = h + "</tbody></table></div></div></div>";

  const sez = $("sez_" + dest);
  sez.querySelector(".sezh").onclick = () => {
    sez.classList.toggle("chiuso");
    try { localStorage.setItem("sez." + dest,
                               sez.classList.contains("chiuso") ? "1" : "0"); } catch(e){}
  };
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
  const rs = "record " + (reg.length ? reg.join(",") : "none") +
             "  \u00b7  self-trigger " + (self.length ? self.join(",") : "none") +
             (off.length ? ", offset " + off.join(",") : "");
  tabellaCanali("tabdig", "V1742 digitizer channels", 32, [
    {chiave: "reg",  titolo: "record",       tipo: "flag"},
    {chiave: "self", titolo: "self-trigger", tipo: "flag"},
    {chiave: "off",  titolo: "offset",       tipo: "testo"},
  ], {reg: reg, self: self, off: srotola(self, off)}, rs, false);

  const cch = numeri(valoreDi("Channels"));
  const cth = numeri(valoreDi("Threshold"));
  // Col modulo spento sono sedici righe di peso morto: parte chiusa, finche'
  // qualcuno non decide altrimenti e allora comanda la sua scelta.
  const cfdOn = valoreDi("Enabled") === "true";
  const cs = cfdOn
    ? "inputs " + (cch.length ? cch.join(",") : "none") +
      (cth.length ? "  \u00b7  " + cth.join(",") + " mV" : "")
    : "module disabled";
  tabellaCanali("tabcfd", "V812 CFD inputs  (module numbering, not the digitizer's)",
    16, [
      {chiave: "cfd",  titolo: "enabled",     tipo: "flag"},
      {chiave: "cthr", titolo: "threshold [mV]", tipo: "testo"},
    ], {cfd: cch, cthr: srotola(cch, cth)}, cs, !cfdOn);
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
    return `<tr><td class="k">${c.etichetta}</td><td style="color:#a8321f">not in the file: add it by hand</td></tr>`;
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
  catch(e){ $("cfg").textContent = "cannot read the configuration"; return; }
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
  // Delega: le caselle vengono ricreate a ogni ricarica del modulo, quindi
  // si ascolta sui contenitori e non sui singoli campi.
  for(const id of ["cfg", "tabdig", "tabcfd"]){
    $(id).oninput  = aggiornaStatoSalva;
    $(id).onchange = aggiornaStatoSalva;
  }
  aggiornaStatoSalva();
}

// Le modifiche restano nel browser finche' non si preme Salva, e il pulsante
// sta in fondo a un modulo lungo con due tabelle scorrevoli: e' facilissimo
// spuntare una casella, lanciare una run e non capire perche' non cambia
// niente. E' successo. Da qui l'avviso, che compare appena qualcosa differisce
// da quello che c'e' nel file.
function aggiornaStatoSalva(){
  if(!CFG) return;
  const n = modificheCorrenti().length;
  $("nonsalvato").style.display = n ? "inline" : "none";
  $("nonsalvato").textContent = "\u25cf " + n + (n === 1 ? " unsaved change" : " unsaved changes");
  $("salva").style.background = n ? "#a8321f" : "#2a78d6";
}

function valoreCampo(c){
  const el = $("f_" + c.sezione + "_" + c.chiave);
  return el ? el.value.trim() : null;
}

// Le differenze fra il form e il file: le usa sia il salvataggio sia la coda,
// cosi' una voce di coda e' "questa configurazione" senza doverla ridescrivere.
function modificheCorrenti(){
  if(!CFG) return [];
  const mod = [], tab = dalleTabelle();
  for(const c of CFG.campi){
    if(!c.presente) continue;
    const v = c.tabella ? tab[c.chiave] : valoreCampo(c);
    if(v !== null && v !== undefined && v !== c.valore)
      mod.push([c.sezione, c.chiave, v]);
  }
  return mod;
}

$("ricarica").onclick = caricaConfig;

$("salva").onclick = async () => {
  if(!CFG) return;
  const mod = modificheCorrenti();
  if(!mod.length){ msg("No changes to save.", true); return; }
  const elenco = mod.map(m => "  " + m[1] + "  ->  " + m[2]).join("\n");
  if(!confirm("Write to the TOML?\n\n" + elenco +
              "\n\nApplies from the next run. A backup is made.")) return;
  $("salva").disabled = true;
  try{
    const r = await fetch("/api/config?token=" + TOKEN, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({mtime: CFG.mtime, chi: $("chi").value, modifiche: mod})
    });
    const d = await r.json();
    msg(d.messaggio, d.esito);
  }catch(e){ msg("Save failed: " + e, false); }
  $("salva").disabled = false;
  caricaConfig();   // rilegge dal file e azzera l'avviso
};

$("applica").onclick = async () => {
  const v = $("caldo").value.trim();
  if(!v){ msg("Enter the offsets to apply.", false); return; }
  if(!confirm("Apply thresholds " + v + " to the RUNNING acquisition?\n\n" +
              "The TOML is untouched: the next run goes back to the file values.")) return;
  try{
    const q = new URLSearchParams({offsets: v, chi: $("chi").value, token: TOKEN});
    const d = await (await fetch("/api/soglie?" + q, {method: "POST"})).json();
    msg(d.messaggio, d.esito);
  }catch(e){ msg("Request failed: " + e, false); }
};

// --- scan -----------------------------------------------------------------
async function avviaScan(tipo, idval, idsec, nome){
  const v = $(idval).value.trim(), s = $(idsec).value.trim();
  const n = v.split(/[\s,]+/).filter(x => x !== "").length;
  if(!confirm("Start the " + nome + " scan?\n\n" + n + " points of " + s +
              " s: about " + Math.round(n * s / 60) + " minutes.\n\n" +
              (tipo === "v812"
                ? "The TOML is changed at every point and restored at the end."
                : "The thresholds of the running acquisition change at every point."))) return;
  try{
    const q = new URLSearchParams({tipo: tipo, valori: v, secondi: s,
                                   chi: $("chi").value, token: TOKEN});
    const d = await (await fetch("/api/scan/avvia?" + q, {method: "POST"})).json();
    msg(d.messaggio, d.esito);
  }catch(e){ msg("Request failed: " + e, false); }
  aggiorna();
}

$("s1go").onclick = () => avviaScan("v1742", "s1val", "s1sec", "self-trigger");
$("s2go").onclick = () => avviaScan("v812",  "s2val", "s2sec", "CFD");
$("sstop").onclick = async () => {
  if(!confirm("Stop the scan?\n\nThe script restores the TOML and the running acquisition " +
              "is closed properly.")) return;
  try{
    const q = new URLSearchParams({chi: $("chi").value, token: TOKEN});
    const d = await (await fetch("/api/scan/ferma?" + q, {method: "POST"})).json();
    msg(d.messaggio, d.esito);
  }catch(e){ msg("Request failed: " + e, false); }
  aggiorna();
};

// --- grafici --------------------------------------------------------------
function mostraGrafico(nome){
  if(!nome){ $("gimg").style.display = "none"; return; }
  // Il parametro t serve solo a non far ripescare al browser una versione
  // precedente con lo stesso nome.
  const url = "/grafico?nome=" + encodeURIComponent(nome) +
              "&token=" + TOKEN + "&t=" + Date.now();
  $("gimg").src = url;
  $("gimg").style.display = "block";
  $("gapri").href = url;
}

// Si elencano le MISURE, non i grafici gia' disegnati: un PNG e' una
// decisione gia' presa sulla scala, un JSON no.
async function caricaGrafici(){
  let d;
  try{ d = await (await fetch("/api/grafici?token=" + TOKEN)).json(); }
  catch(e){ return; }
  const m = d.misure || [];
  const scelti = new Set(Array.from($("gsel").selectedOptions).map(o => o.value));
  $("gsel").innerHTML = m.map(x => {
    const q = new Date(x.quando * 1000).toLocaleString();
    const ch = x.canali ? "  ch " + x.canali.join(",") : "";
    return `<option value="${x.nome}">${x.tipo}${ch}  —  ${x.punti} pts  —  ${q}</option>`;
  }).join("");
  $("gvuoto").textContent = m.length ? "" :
    "No measurements in plots/. One appears as soon as a scan finishes.";
  if(m.length){
    let qualcuno = false;
    for(const o of $("gsel").options)
      if(scelti.has(o.value)){ o.selected = true; qualcuno = true; }
    if(!qualcuno) $("gsel").options[0].selected = true;
  }
}

async function disegna(){
  const scelti = Array.from($("gsel").selectedOptions).map(o => o.value);
  if(!scelti.length){ msg("Select at least one measurement.", false); return; }
  const q = new URLSearchParams({token: TOKEN, chi: $("chi").value,
                                 logy: $("glog").checked ? "1" : "0",
                                 perch: $("gperch").checked ? "1" : "0",
                                 canali: $("gchan").value.trim(),
                                 mvoff: $("gmvoff").value.trim()});
  for(const s of scelti) q.append("misura", s);
  $("gdraw").disabled = true;
  // Con "per channel" il disegno rilegge le forme d'onda dal file della run,
  // che a run finita e' compresso: minuti, non secondi. Senza dirlo, la
  // pagina sembra non aver fatto niente e si riclicca.
  msg($("gperch").checked
      ? "Drawing. Per channel rereads the waveforms from the run file: if the "
        + "run is over the file is compressed, so this takes minutes."
      : "Drawing.", true);
  try{
    const d = await (await fetch("/api/disegna?" + q, {method: "POST"})).json();
    if(d.esito && d.png) mostraGrafico(d.png); else msg(d.messaggio, false);
  }catch(e){ msg("Request failed: " + e, false); }
  $("gdraw").disabled = false;
}

$("gdraw").onclick = disegna;
$("glog").onchange = disegna;
$("gperch").onchange = disegna;
$("gchan").onchange = disegna;
$("ggo").onclick = caricaGrafici;

// --- coda -----------------------------------------------------------------
const COLORE_STATO = {"pending":"#6b6a65", "running":"#15603a",
                      "done":"#2a78d6", "failed":"#a8321f", "interrupted":"#eb6834"};

function disegnaCoda(c){
  const v = (c && c.voci) || [];
  if(!v.length){
    $("codatab").innerHTML = '<span style="color:#6b6a65;font-size:12px">queue empty</span>';
    return;
  }
  const righe = v.map(x => {
    const m = (x.modifiche || []).map(y => y[1] + "=" + y[2]).join(", ") || "configuration as in the file";
    const col = COLORE_STATO[x.stato] || "#6b6a65";
    const esito = [x.run || "", x.eventi != null ? x.eventi + " ev" : "", x.messaggio || ""]
                  .filter(s => s).join(" · ");
    return `<tr>
      <td style="color:#6b6a65">${x.id}</td>
      <td><b>${x.nome}</b></td>
      <td style="font-size:11px;color:#52514e">${m}</td>
      <td>${x.secondi ? x.secondi + " s" : "until NEvents"}</td>
      <td style="color:${col};font-weight:600">${x.stato}</td>
      <td style="font-size:11px;color:#52514e">${esito}</td>
      <td><button data-id="${x.id}" class="crm" style="background:#ececea;padding:2px 8px">remove</button></td>
    </tr>`;
  }).join("");
  $("codatab").innerHTML = `<table style="font-size:12px"><thead><tr style="color:#6b6a65">
    <td>#</td><td>name</td><td>changes</td><td>duration</td><td>status</td><td>outcome</td><td></td>
    </tr></thead><tbody>${righe}</tbody></table>`;
  for(const b of document.querySelectorAll(".crm"))
    b.onclick = async () => {
      const q = new URLSearchParams({id: b.dataset.id, chi: $("chi").value, token: TOKEN});
      const d = await (await fetch("/api/coda/rimuovi?" + q, {method:"POST"})).json();
      msg(d.messaggio, d.esito); aggiorna();
    };
}

async function codaAzione(azione, conferma){
  if(conferma && !confirm(conferma)) return;
  try{
    const q = new URLSearchParams({chi: $("chi").value, token: TOKEN});
    const d = await (await fetch("/api/coda/" + azione + "?" + q, {method:"POST"})).json();
    msg(d.messaggio, d.esito);
  }catch(e){ msg("Request failed: " + e, false); }
  aggiorna();
}

$("cadd").onclick = async () => {
  const mod = modificheCorrenti();
  const nome = $("cnome").value.trim(), sec = $("csec").value.trim();
  const descr = mod.length ? mod.map(m => m[1] + "=" + m[2]).join(", ")
                           : "no changes: the configuration as it is in the file";
  if(!confirm("Add to the queue?\n\n" + (nome || "(unnamed)") + "\n" + descr +
              "\n" + (sec ? sec + " s" : "until NEvents"))) return;
  try{
    const r = await fetch("/api/coda/aggiungi?token=" + TOKEN, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({nome: nome, secondi: sec, modifiche: mod, chi: $("chi").value})
    });
    const d = await r.json();
    msg(d.messaggio, d.esito);
    if(d.esito){ $("cnome").value = ""; }
  }catch(e){ msg("Request failed: " + e, false); }
  aggiorna();
};

$("cgo").onclick   = () => codaAzione("avvia",
  "Start the queue?\n\nThe TOML is saved now and restored at the end.");
$("cstop").onclick = () => codaAzione("ferma",
  "Stop the queue?\n\nThe running acquisition is closed properly and the TOML restored.");
$("cretry").onclick = () => codaAzione("riprova",
  "Put interrupted or failed entries back in the queue?");
$("cclr").onclick  = () => codaAzione("svuota", "Clear the queue?");

caricaGrafici();
caricaConfig();
// -- alimentatore dei SiPM ------------------------------------------------
let PSU = null;
async function psuAzione(azione, valore, conferma){
  if(conferma && !confirm(conferma)) return;
  try{
    const q = new URLSearchParams({chi: $("chi").value, token: TOKEN});
    if(valore !== undefined) q.set("valore", valore);
    const d = await (await fetch("/api/psu/" + azione + "?" + q, {method: "POST"})).json();
    msg(d.messaggio, d.esito);
  }catch(e){ msg("Request failed: " + e, false); }
  aggiorna(); psuSerie();
}
function psuNum(id){
  const v = parseFloat($(id).value.replace(",", "."));
  if(!isFinite(v)){ msg("Write a number first.", false); return null; }
  return v;
}
$("psuvgo").onclick = () => {
  const v = psuNum("psuvset"); if(v === null || !PSU) return;
  const da = PSU.lettura ? PSU.lettura.v_set.toFixed(2) : "?";
  if(v > PSU.vmax){ msg(v + " V is above the " + PSU.vmax + " V limit.", false); return; }
  psuAzione("tensione", v, "Ramp the SiPM bias from " + da + " V to " + v.toFixed(2) + " V?" +
            (PSU.lettura && PSU.lettura.uscita ? "\n\nThe output is ON: the SiPMs will see it." : ""));
};
$("psuon").onclick = () => psuAzione("on", undefined,
  "Switch the output ON at " + (PSU && PSU.lettura ? PSU.lettura.v_set.toFixed(2) : "?") + " V?");
// Spegnere non chiede conferma: e' il pulsante di quando qualcosa va storto.
$("psuoff").onclick = () => psuAzione("off");
$("psuigo").onclick = () => {
  const v = psuNum("psuilim"); if(v === null) return;
  psuAzione("ilim", v * 1e-6, "Set the current limit to " + v + " \u00b5A?");
};
$("psuogo").onclick = () => {
  const v = psuNum("psuovp"); if(v === null) return;
  psuAzione("ovp", v, "Set the over-voltage protection to " + v + " V?");
};
$("psufind").onclick = () => psuAzione("trova");

function aggiornaPsu(p){
  PSU = p;
  const box = $("b_psu");
  box.style.display = p ? "" : "none";
  if(!p) return;
  const l = p.lettura;
  // Una lettura vecchia non deve sembrare viva: lo strumento puo' aver
  // cambiato indirizzo o essere stato spento, e i numeri restano li'.
  const eta = l ? (Date.now() / 1000 - l.t) : Infinity;
  const fresca = eta < 4 * p.periodo;
  $("psuerr").style.display = p.errore ? "block" : "none";
  $("psuerr").textContent = p.errore ? "Supply not answering \u2014 " + p.errore +
      (l ? " (last reading " + Math.round(eta) + " s ago)" : "") : "";
  $("psufind").style.display = p.errore ? "" : "none";
  $("psuv").textContent = l ? l.v_out.toFixed(2) : "\u2014";
  $("psui").textContent = l ? (l.i_out * 1e6).toFixed(2) : "\u2014";
  $("psuv").style.color = $("psui").style.color = fresca ? "" : "#b5b5b0";
  $("psuout").textContent = !l ? "\u2026" : (l.uscita ? "OUTPUT ON" : "output off");
  $("psuout").className = "stato " + (l && l.uscita ? "corso" : "ferma");
  $("psurampa").textContent = p.rampa
      ? "ramping to " + p.rampa.a.toFixed(2) + " V" + (p.rampa.v != null ? " (now " + p.rampa.v.toFixed(1) + ")" : "")
      : "";
  if(l){
    let info = "set " + l.v_set.toFixed(2) + " V \u00b7 limit " + (l.i_lim * 1e6).toFixed(1) +
               " \u00b5A \u00b7 OVP " + l.ovp.toFixed(1) + " V \u00b7 allowed up to " + p.vmax + " V \u00b7 " + p.host;
    // L'OVP e' l'unica protezione che vale anche per la manopola del
    // pannello: il limite della pagina non la ferma.
    if(l.ovp > p.vmax + 3) info += "  \u2014  OVP above the limit: the front panel can still go higher";
    $("psuinfo").textContent = info;
    if(!$("psuvset").value && document.activeElement !== $("psuvset")) $("psuvset").placeholder = l.v_set.toFixed(2);
    if(document.activeElement !== $("psuilim")) $("psuilim").placeholder = (l.i_lim * 1e6).toFixed(1);
    if(document.activeElement !== $("psuovp")) $("psuovp").placeholder = l.ovp.toFixed(1);
  }
  $("psuvgo").disabled = $("psuon").disabled = $("psuigo").disabled = $("psuogo").disabled = !!p.rampa;
  riass("b_psu", l ? l.v_out.toFixed(2) + " V  " + (l.i_out * 1e6).toFixed(2) + " \u00b5A  " +
                     (l.uscita ? "ON" : "off") : (p.errore ? "not answering" : ""));
}

let PSU_ORE = 6, PSU_DATI = [];
try { PSU_ORE = parseFloat(localStorage.getItem("psu.ore")) || 6; } catch(e){}
document.querySelectorAll("#psufin button").forEach(b => {
  b.onclick = () => {
    PSU_ORE = parseFloat(b.dataset.ore);
    try { localStorage.setItem("psu.ore", PSU_ORE); } catch(e){}
    psuSerie();
  };
});
async function psuSerie(){
  document.querySelectorAll("#psufin button").forEach(b =>
    b.classList.toggle("sel", parseFloat(b.dataset.ore) === PSU_ORE));
  if(!PSU || $("b_psu").classList.contains("chiuso")) return;
  try{
    const d = await (await fetch("/api/psu/serie?ore=" + PSU_ORE + "&token=" + TOKEN)).json();
    PSU_DATI = d.punti || [];
  }catch(e){ return; }
  psuDisegna(null);
}
// Due grafici piccoli, uno per grandezza, ognuno con la sua scala: tensione
// e corrente su due assi y dello stesso grafico si leggono male. La scala ha
// un'escursione minima (50 mV, 0.05 uA), altrimenti il rumore dell'ultima
// cifra riempirebbe il riquadro e sembrerebbe una deriva.
// La finestra davvero mostrata. Non si disegnano sei ore di bianco quando le
// letture sono di tre minuti: il bordo sinistro si ferma al primo dato. Il
// minuto di larghezza minima evita che due letture vicine diano una scala
// senza senso. La usano il disegno E il puntamento del mouse: due copie di
// questo conto scivolerebbero via una dall'altra al primo ritocco.
function psuFinestra(){
  const t1 = Date.now() / 1000;
  const chiesto = t1 - PSU_ORE * 3600;
  const primo = PSU_DATI.length ? PSU_DATI[0][0] : chiesto;
  return [Math.min(Math.max(chiesto, primo), t1 - 60), t1];
}

// Un passo di griglia leggibile: 1, 2, 2.5 o 5 per una potenza di dieci.
// Senza, i limiti erano il minimo e il massimo grezzi e sull'asse comparivano
// numeri come 54.07 e 52.02, che non dicono niente a colpo d'occhio.
function psuPasso(intervallo, quanti){
  if(!(intervallo > 0)) return 1;
  const g = Math.pow(10, Math.floor(Math.log10(intervallo / quanti)));
  for(const m of [1, 2, 2.5, 5, 10]) if(intervallo / (m * g) <= quanti) return m * g;
  return 10 * g;
}

function psuGrafico(cv, col, scala, minimo, dec, hover){
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight;
  cv.width = W * dpr; cv.height = H * dpr;
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);
  const L = 44, R = 4, T = 4, B = 14;   // in basso ci vanno le etichette del tempo
  g.font = "10px system-ui"; g.fillStyle = "#6b6a65";
  if(PSU_DATI.length < 2){ g.fillText("collecting readings\u2026", L, H / 2); return; }
  const [t0, t1] = psuFinestra();
  const ys = PSU_DATI.map(p => p[col] * scala);
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if(hi - lo < minimo){ const c = (hi + lo) / 2; lo = c - minimo / 2; hi = c + minimo / 2; }
  // Un margine del 12% sopra e sotto: con i limiti incollati al minimo e al
  // massimo la traccia striscia sulla cornice e non si vede piu' dove
  // oscilla. Poi i limiti si arrotondano al passo.
  const aria = (hi - lo) * 0.12; lo -= aria; hi += aria;
  // TRE righe, non una di piu': il riquadro e' alto 62 px e con cinque
  // etichette i numeri si sovrappongono fra loro. L'arrotondamento verso
  // l'esterno puo' aggiungere una riga, quindi si raddoppia il passo finche'
  // non ne restano due intervalli.
  let passo = psuPasso(hi - lo, 2), lo0 = lo, hi0 = hi;
  for(let k = 0; k < 8; k++){
    lo = Math.floor(lo0 / passo) * passo;
    hi = Math.ceil(hi0 / passo) * passo;
    if((hi - lo) / passo <= 2.001) break;
    passo *= 2;
  }
  const nd = Math.max(dec === 0 ? 0 : 1, Math.min(4, -Math.floor(Math.log10(passo))));
  const X = t => L + (t - t0) / (t1 - t0) * (W - L - R);
  const Y = v => T + (hi - v) / (hi - lo) * (H - T - B);
  g.strokeStyle = "#ececea"; g.lineWidth = 1;
  g.textBaseline = "middle"; g.textAlign = "left";
  for(let v = lo; v <= hi + passo / 2; v += passo){
    g.beginPath(); g.moveTo(L, Y(v)); g.lineTo(W - R, Y(v)); g.stroke();
    const y = Math.min(H - B - 5, Math.max(T + 5, Y(v)));
    g.fillText(v.toFixed(nd), 2, y);
  }
  g.strokeStyle = "#2a78d6"; g.lineWidth = 2; g.lineJoin = "round";
  g.beginPath();
  PSU_DATI.forEach((p, i) => { const x = X(p[0]), y = Y(ys[i]); i ? g.lineTo(x, y) : g.moveTo(x, y); });
  g.stroke();
  // Quanto tempo copre l'asse, con l'unita' scritta: senza, un grafico di tre
  // minuti e uno di sei ore sono identici.
  const dur = t1 - t0;
  const [u, nome] = dur < 180 ? [1, "s"] : dur < 10800 ? [60, "min"] : [3600, "h"];
  const fmt = x => (x < 10 ? x.toFixed(1) : x.toFixed(0));
  g.fillStyle = "#6b6a65"; g.textBaseline = "bottom";
  g.textAlign = "left";   g.fillText("\u2212" + fmt(dur / u) + " " + nome, L, H);
  g.textAlign = "center"; g.fillText("\u2212" + fmt(dur / 2 / u) + " " + nome,
                                     (L + W - R) / 2, H);
  g.textAlign = "right";  g.fillText("now", W - R, H);
  g.textAlign = "left";

  if(hover !== null){
    const p = PSU_DATI[hover];
    g.strokeStyle = "#9a9a94"; g.lineWidth = 1;
    g.beginPath(); g.moveTo(X(p[0]), T); g.lineTo(X(p[0]), H - B); g.stroke();
    g.fillStyle = "#2a78d6"; g.beginPath(); g.arc(X(p[0]), Y(ys[hover]), 4, 0, 7); g.fill();
  }
}
function psuDisegna(hover){
  psuGrafico($("psugv"), 1, 1, 0.05, 2, hover);
  psuGrafico($("psugi"), 2, 1e6, 0.05, 2, hover);
  if(hover === null){ $("psuhover").textContent = ""; return; }
  const p = PSU_DATI[hover];
  $("psuhover").textContent = new Date(p[0] * 1000).toLocaleTimeString() + "   " +
      p[1].toFixed(3) + " V   " + (p[2] * 1e6).toFixed(3) + " \u00b5A";
}
["psugv", "psugi"].forEach(id => {
  const cv = $(id);
  cv.onmousemove = ev => {
    if(PSU_DATI.length < 2) return;
    const r = cv.getBoundingClientRect();
    const [t0, t1] = psuFinestra();
    const t = t0 + (ev.clientX - r.left - 44) / (r.width - 48) * (t1 - t0);
    let k = 0;
    PSU_DATI.forEach((p, i) => { if(Math.abs(p[0] - t) < Math.abs(PSU_DATI[k][0] - t)) k = i; });
    psuDisegna(k);
  };
  cv.onmouseleave = () => psuDisegna(null);
});
window.addEventListener("resize", () => psuDisegna(null));

aggiorna();
setInterval(aggiorna, 2000);
setTimeout(psuSerie, 500);
setInterval(psuSerie, 15000);
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

        def _inoltra(self, resto, query):
            """Gira una richiesta al monitor e riporta indietro la risposta.

            Solo GET: il monitor non espone altro. Il corpo si legge tutto in
            memoria invece di essere ritrasmesso a pezzi perche' le risposte
            sono una pagina o un PNG di qualche centinaio di kB, e il tempo se
            ne va a disegnare i grafici, non a copiarli.
            """
            url = "http://127.0.0.1:%d/%s" % (ctrl.porta_monitor, resto)
            if query:
                url += "?" + query
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    corpo = r.read()
                    tipo = r.headers.get("Content-Type", "application/octet-stream")
            except urllib.error.HTTPError as e:
                return self._manda(e.code, "text/plain", str(e).encode())
            except (urllib.error.URLError, OSError) as e:
                # Il caso tipico e' il monitor spento: dirlo qui evita la
                # pagina bianca del browser, che non sa niente di tutto cio'.
                return self._manda(
                    502, "text/html; charset=utf-8",
                    ("<p style=\"font:15px system-ui;padding:24px\">The monitor is not "
                     "answering on port %d of the DAQ machine.<br>Go back to "
                     "<a href=\"/\">DAQ Control</a> and press <b>Start monitor</b>."
                     "<br><br><small>%s</small></p>"
                     % (ctrl.porta_monitor, e)).encode())
            self.send_response(200)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            if getattr(self, "_cookie_token", False):
                self.send_header("Set-Cookie",
                                 "daqtoken=%s; Path=/monitor; SameSite=Strict" % token)
            self.end_headers()
            return self.wfile.write(corpo)

        def _autorizzato(self, qs):
            if not token:
                return True
            dato = qs.get("token", [""])[0] or self.headers.get("X-Token", "")
            if dato == token:
                return True
            # Anche da cookie: la pagina del monitor, servita qui sotto
            # /monitor/, chiede stats.json e i PNG con indirizzi relativi e non
            # ha modo di portarsi dietro il token. Senza questo, con un token
            # configurato il monitor inoltrato mostrerebbe la pagina e poi
            # nient'altro.
            for pezzo in (self.headers.get("Cookie") or "").split(";"):
                nome, _, val = pezzo.strip().partition("=")
                if nome == "daqtoken" and val == token:
                    return True
            return False

        def do_GET(self):
            parti = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parti.query)
            if parti.path == "/":
                return self._manda(200, "text/html; charset=utf-8", PAGINA.encode())
            # Il monitor, servito attraverso QUESTA porta. Esiste perche' il
            # monitor gira su una porta sua, e chi guarda la pagina da fuori
            # -- VPN, inoltro di porta di VS Code, il collegamento diretto che
            # verra' -- quasi sempre ha inoltrata solo questa. Il link diretto
            # a hostname:8765 funziona dalla rete del laboratorio e non
            # altrove, e l'errore che ne esce ("Safari non puo' connettersi")
            # non distingue fra monitor spento e porta non raggiungibile.
            # Qui invece: se vedi la pagina di controllo, vedi il monitor.
            if parti.path == "/monitor" or parti.path.startswith("/monitor/"):
                if not self._autorizzato(qs):
                    return self._manda(403, "text/plain", b"token")
                if parti.path == "/monitor":
                    # Senza la barra finale il browser risolverebbe gli
                    # indirizzi relativi della pagina del monitor
                    # ("stats.json", "waveforms.png") sulla radice di questo
                    # servizio, e non troverebbe niente.
                    self.send_response(302)
                    self.send_header("Location", "/monitor/" +
                                     ("?" + parti.query if parti.query else ""))
                    self.end_headers()
                    return
                if token and qs.get("token", [""])[0] == token:
                    self._cookie_token = True
                return self._inoltra(parti.path[len("/monitor/"):], parti.query)

            if parti.path == "/api/grafici":
                if not self._autorizzato(qs):
                    return self._json({"errore": "token mancante o sbagliato"}, 403)
                return self._json({"grafici": elenco_grafici(),
                                   "misure": elenco_misure()})

            if parti.path == "/grafico":
                if not self._autorizzato(qs):
                    return self._manda(403, "text/plain", b"token")
                percorso = percorso_grafico(qs.get("nome", [""])[0])
                if not percorso:
                    return self._manda(404, "text/plain", b"non trovato")
                with open(percorso, "rb") as f:
                    dati = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(dati)))
                # I grafici vengono rifatti con lo stesso nome quando si
                # ripete uno scan: senza questo il browser mostrerebbe quello
                # vecchio convinto di avere ragione.
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return self.wfile.write(dati)

            if parti.path == "/api/psu/serie":
                if not self._autorizzato(qs):
                    return self._json({"errore": "token mancante o sbagliato"}, 403)
                if not ctrl.psu:
                    return self._json({"punti": []})
                try:
                    ore = min(24.0, max(0.1, float(qs.get("ore", ["6"])[0])))
                except ValueError:
                    ore = 6.0
                return self._json(ctrl.psu.serie(ore))

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
                esito, messaggio = ctrl._con_hv(*ctrl.avvia())
                registra(chi, da, "avvia", messaggio)

            elif parti.path == "/api/ferma":
                esito, messaggio = ctrl._con_hv(*ctrl.ferma())
                registra(chi, da, "ferma", messaggio)

            elif parti.path == "/api/monitor":
                azione = qs.get("azione", [""])[0]
                if azione == "avvia":
                    esito, messaggio = ctrl.avvia_monitor()
                elif azione == "ferma":
                    esito, messaggio = ctrl.ferma_monitor()
                else:
                    return self._manda(404, "text/plain", b"not found")
                registra(chi, da, "monitor/" + azione, messaggio)

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

            elif parti.path == "/api/disegna":
                esito, messaggio, png = disegna_misure(
                    qs.get("misura", []), qs.get("logy", ["0"])[0] == "1",
                    qs.get("perch", ["0"])[0] == "1", qs.get("canali", [""])[0],
                    qs.get("mvoff", [""])[0])
                extra["png"] = png

            elif parti.path.startswith("/api/coda/"):
                azione = parti.path.rsplit("/", 1)[1]
                if azione == "aggiungi":
                    try:
                        n = int(self.headers.get("Content-Length", 0))
                        corpo = json.loads(self.rfile.read(n) or b"{}")
                    except (ValueError, OSError) as e:
                        return self._json({"esito": False,
                                           "messaggio": "richiesta illeggibile: %s" % e})
                    chi = (corpo.get("chi") or chi).strip()[:40]
                    esito, messaggio = ctrl.coda_aggiungi(corpo.get("nome"),
                                                          corpo.get("secondi"),
                                                          corpo.get("modifiche", []))
                elif azione == "rimuovi":
                    esito, messaggio = ctrl.coda_rimuovi(qs.get("id", [""])[0])
                elif azione == "riprova":
                    esito, messaggio = ctrl.coda_riprova()
                elif azione == "svuota":
                    esito, messaggio = ctrl.coda_svuota()
                elif azione == "avvia":
                    esito, messaggio = ctrl.coda_avvia()
                elif azione == "ferma":
                    esito, messaggio = ctrl.coda_ferma()
                else:
                    return self._manda(404, "text/plain", b"not found")
                registra(chi, da, "coda/" + azione, messaggio)

            elif parti.path == "/api/scan/avvia":
                esito, messaggio = ctrl.avvia_scan(qs.get("tipo", [""])[0],
                                                   qs.get("valori", [""])[0],
                                                   qs.get("secondi", ["20"])[0])
                registra(chi, da, "avvia scan", messaggio)

            elif parti.path == "/api/scan/ferma":
                esito, messaggio = ctrl.ferma_scan()
                registra(chi, da, "ferma scan", messaggio)

            elif parti.path.startswith("/api/psu/"):
                if not ctrl.psu:
                    return self._json({"esito": False,
                                       "messaggio": "The controller runs without a supply (--psu-host '')."})
                azione = parti.path.rsplit("/", 1)[1]
                try:
                    valore = float(qs.get("valore", ["nan"])[0])
                except ValueError:
                    valore = float("nan")
                numerico = {"tensione": ctrl.psu.tensione,
                            "ilim": ctrl.psu.limite_corrente, "ovp": ctrl.psu.ovp}
                if azione in numerico:
                    if valore != valore:
                        return self._json({"esito": False, "messaggio": "Not a number."})
                    esito, messaggio = numerico[azione](valore)
                elif azione == "on":
                    esito, messaggio = ctrl.psu.uscita(True)
                elif azione == "off":
                    esito, messaggio = ctrl.psu.uscita(False)
                elif azione == "trova":
                    esito, messaggio = ctrl.psu.trova()
                else:
                    return self._manda(404, "text/plain", b"not found")
                registra(chi, da, "psu/" + azione, messaggio)

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
    global PSU_LOG
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
    ap.add_argument("--monitor-port", type=int, default=PORTA_MONITOR,
                    help="porta del monitor, per il link e per avviarlo "
                         "(default %d)" % PORTA_MONITOR)
    ap.add_argument("--monitor-args", default=" ".join(ARGS_MONITOR),
                    help="opzioni con cui avviare il monitor "
                         "(default: %(default)s)")
    ap.add_argument("--log", default=os.path.join(ROOT, "data", "daq-console.log"),
                    help="dove finisce l'uscita della DAQ")
    ap.add_argument("--psu-host", default=psu_control.DEFAULT_HOST,
                    help="alimentatore dei SiPM; '' per farne a meno")
    ap.add_argument("--psu-log", default=PSU_LOG,
                    help="CSV delle letture: una prova su un'altra porta deve "
                         "scriverne uno suo, non mescolarsi a quello vero")
    args = ap.parse_args()

    args_mon = shlex.split(args.monitor_args)
    # La porta del monitor deve comparire anche fra le sue opzioni, se no il
    # controllore sonderebbe una porta e il monitor ne aprirebbe un'altra.
    if "-p" not in args_mon and "--port" not in args_mon:
        args_mon += ["-p", str(args.monitor_port)]
    PSU_LOG = args.psu_log
    ctrl = Controllo(args.config, args.log, args.monitor_port, args_mon, args.psu_host)
    stampa = lambda s: print(s, flush=True)
    stampa("Configurazione : %s" % ctrl.toml)
    stampa("Binario        : %s%s" % (BINARIO, "" if os.path.exists(BINARIO) else "   NON ESISTE"))
    stampa("Uscita DAQ     : %s" % args.log)
    stampa("Alimentatore   : %s" % ("%s, letture in %s" % (args.psu_host, PSU_LOG)
                                    if args.psu_host else "nessuno"))
    if args.token:
        stampa("Token          : attivo")
    pid = trova_daq()
    stampa("DAQ            : %s" % ("in esecuzione, pid %d" % pid if pid else "ferma"))

    # Una coda rimasta "attiva" da prima vuol dire che il controllore e' morto
    # mentre la eseguiva: il thread che la portava avanti non c'e' piu'. La si
    # chiude e si rimette il TOML, invece di lasciare uno stato che dice il
    # falso. Ripartira' chi vuole, sapendo da dove.
    if ctrl.coda_attiva():
        ctrl._chiudi_coda("Interrupted by a controller restart: resume it by hand.")
        stampa("Coda           : era rimasta attiva, chiusa e TOML ripristinato")

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
