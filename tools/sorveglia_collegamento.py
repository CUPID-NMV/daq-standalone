#!/usr/bin/env python3
"""Sorveglia da fuori il collegamento fra la DAQ e il bridge VME V4718, per capire perche' cade.

Le run perdono il collegamento col digitizer ("ReadData failed ... giving up", codice -1) quasi
solo nei primi minuti dopo l'avvio: al LNGS 43 cadute fino all'8/11, tutte tranne una entro
400 s da quando la DAQ ha aperto la scheda. PC scarico e scheda di rete senza errori.

Questo programma NON tocca la DAQ ne' la scheda. Ogni PASSO_S guarda:
- le connessioni TCP della DAQ verso il bridge (porta 4718): stato, ritrasmissioni, rto,
  millisecondi dall'ultimo invio e dall'ultima ricezione, byte ricevuti;
- se il bridge accetta una connessione sulla sua pagina web (porta 80): dice se il sistema del
  bridge e' vivo, indipendentemente dalla sessione della DAQ. Il bridge non risponde al ping;
- l'indirizzo fisico (MAC) del bridge nella tabella ARP: se cambia, c'e' un secondo
  apparecchio con lo stesso indirizzo IP (gli indirizzi qui vengono dal DHCP);
- stato ed errori della scheda di rete, contatori TCP del sistema, carico, e se stanno girando
  la compressione e l'invio al cloud della run precedente (pigz, cp_data);
- il log della run in corso, per accorgersi del primo "ReadData failed".

Tiene in memoria gli ultimi ANTE_S secondi. Quando succede qualcosa (un ReadData fallito, una
connessione che ritrasmette, il bridge che non risponde, il MAC che cambia, un errore della
scheda di rete) scrive su disco quei secondi e i POST_S seguenti, e una riga nel registro.
Alla prossima caduta si vede quale dei tre casi e':
- la sessione si chiude con un reset dal bridge          -> firmware del bridge, CAEN;
- i pacchetti smettono di arrivare (ritrasmissioni) e la pagina web non risponde
                                                          -> bridge bloccato, cavo o switch;
- la pagina web risponde e la connessione e' viva, ma i dati non arrivano
                                                          -> la scheda non risponde sul VME.

Si ferma con un segnale al suo PID (scritto in USCITA/sorveglia.pid), mai con pkill -f.
"""
# ===================== CONFIGURAZIONE =====================
BRIDGE       = "192.168.99.105"
PORTA_DAQ    = 4718
PORTA_PROVA  = 80
SCHEDA_RETE  = "eno1"
PASSO_S      = 0.25    # un campione ogni quarto di secondo
PROVA_OGNI_S = 1.0     # una connessione di prova alla pagina web del bridge al secondo
PROVA_TIMEOUT_S = 0.8
ANTE_S       = 120     # secondi tenuti in memoria e scritti quando succede qualcosa
POST_S       = 60      # secondi scritti dopo l'ultimo fatto
BATTITO_S    = 600     # ogni quanto una riga "vivo" nel registro
USCITA       = None    # None: data/collegamento/ nella radice del repository
# ==========================================================

import collections, json, os, re, signal, socket, subprocess, sys, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USCITA = USCITA or os.path.join(ROOT, "data", "collegamento")
os.makedirs(USCITA, exist_ok=True)
REGISTRO = os.path.join(USCITA, "registro.log")

CONTATORI_TCP = ["RetransSegs", "EstabResets", "OutRsts", "AttemptFails", "InErrs"]
CONTATORI_TCPEXT = ["TCPTimeouts", "TCPAbortOnTimeout", "TCPAbortOnData", "TCPAbortOnClose",
                    "TCPLostRetransmit", "TCPSynRetrans"]
CONTATORI_RETE = ["rx_errors", "tx_errors", "rx_dropped", "tx_dropped", "rx_missed_errors",
                  "rx_crc_errors", "rx_fifo_errors", "tx_fifo_errors", "tx_carrier_errors"]
CAMPI_SS = ["rto", "rtt", "retrans", "lastsnd", "lastrcv", "lastack", "unacked", "lost",
            "bytes_received", "bytes_acked", "backoff", "probes"]


def adesso():
    return time.strftime("%Y-%m-%d %H:%M:%S") + ".%03d" % (time.time() % 1 * 1000)


def registra(testo):
    riga = "%s  %s" % (adesso(), testo)
    with open(REGISTRO, "a") as f:
        f.write(riga + "\n")
    print(riga, flush=True)


# ---------------------------------------------------------------- letture
def connessioni():
    """Le connessioni TCP verso il bridge sulla porta della DAQ, con le statistiche del kernel."""
    try:
        out = subprocess.run(["ss", "-tinpH", "state", "all", "dst", BRIDGE, "dport", "=", ":%d" % PORTA_DAQ],
                             capture_output=True, text=True, timeout=2).stdout
    except Exception as e:
        return [{"errore": str(e)}]
    righe, cur = [], None
    for l in out.splitlines():
        if not l.strip():
            continue
        if not l[0].isspace():              # riga principale: stato, code, indirizzi, processo
            p = l.split()
            cur = {"stato": p[0], "rq": int(p[1]), "sq": int(p[2]), "locale": p[3].rsplit(":", 1)[-1]}
            m = re.search(r'pid=(\d+),fd=(\d+)', l)
            if m:
                cur["pid"], cur["fd"] = int(m.group(1)), int(m.group(2))
            righe.append(cur)
        elif cur is not None:               # riga delle statistiche
            for k in CAMPI_SS:
                m = re.search(r'(?<![\w])%s:(\S+)' % k, l)
                if m:
                    v = m.group(1)
                    try:
                        v = int(v)
                    except ValueError:
                        try:
                            v = float(v)
                        except ValueError:
                            pass
                    cur[k] = v
    return righe


def contatori_tcp():
    c = {}
    for nome, chiavi in (("/proc/net/snmp", CONTATORI_TCP), ("/proc/net/netstat", CONTATORI_TCPEXT)):
        try:
            righe = [l.split() for l in open(nome) if l.startswith(("Tcp:", "TcpExt:"))]
            for i in range(0, len(righe) - 1, 2):
                for k, v in zip(righe[i][1:], righe[i + 1][1:]):
                    if k in chiavi:
                        c[k] = int(v)
        except OSError:
            pass
    return c


def scheda_rete():
    base = "/sys/class/net/%s/" % SCHEDA_RETE
    d = {}
    try:
        d["carrier"] = open(base + "carrier").read().strip()
        d["oper"] = open(base + "operstate").read().strip()
        for k in CONTATORI_RETE:
            d[k] = int(open(base + "statistics/" + k).read())
    except OSError as e:
        d["errore"] = str(e)
    return d


def arp_bridge():
    try:
        for l in open("/proc/net/arp"):
            p = l.split()
            if p and p[0] == BRIDGE:
                return "%s %s" % (p[3], p[2])     # MAC e flag (0x2 = completo)
    except OSError:
        pass
    return None


def viva(pid):
    """Vera se il processo esiste e non e' uno zombie. Una DAQ finita resta zombie finche' il
    controllore non ne raccoglie l'uscita, anche un paio di secondi, e intanto la run dopo e'
    gia' partita: contandola, la fine della run risultava dopo l'inizio della successiva. Le
    sue connessioni il kernel le ha gia' chiuse all'uscita."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def processi():
    """PID delle DAQ accese e i processi di fine run che interessano (compressione, cloud)."""
    daq, altri = [], set()
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            comm = open("/proc/%s/comm" % d).read().strip()
            if comm == "DAQ-WC":
                if viva(int(d)):
                    daq.append(int(d))
            elif comm in ("pigz", "gzip", "cp_data", "cp_data.py"):
                altri.add(comm)
            elif comm.startswith("python"):
                cmd = open("/proc/%s/cmdline" % d, "rb").read().replace(b"\0", b" ").decode(errors="replace")
                for chiave in ("archivia.py", "cp_data", "efficienza"):
                    if chiave in cmd:
                        altri.add(chiave)
        except OSError:
            continue
    return sorted(daq), sorted(altri)


def log_della_daq(pid):
    """Il .log della run, fra i file aperti dalla DAQ (come fa il controllore). La DAQ tiene
    aperta anche data/daq-console.log, la sua uscita a video, che il controllore riscrive a
    ogni run: quello giusto e' il .log col nome della run, accanto al file dei dati."""
    try:
        for fd in os.listdir("/proc/%d/fd" % pid):
            try:
                p = os.readlink("/proc/%d/fd/%s" % (pid, fd))
            except OSError:
                continue
            if p.endswith(".log") and "/data" in p and os.path.basename(p) != "daq-console.log":
                return p
    except OSError:
        pass
    return None


# ---------------------------------------------------------------- prova del bridge, in un thread
# La connessione di prova puo' attendere fino a PROVA_TIMEOUT_S se il bridge non risponde: in un
# thread a parte, cosi' il campionamento resta a PASSO_S proprio quando conta.
prova = {"t": None, "ok": None, "ms": None, "errore": None}
prova_lock = threading.Lock()
fine = threading.Event()


def prova_bridge():
    while not fine.is_set():
        t0 = time.time()
        esito = {"t": round(t0, 3), "ok": False, "ms": None, "errore": None}
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(PROVA_TIMEOUT_S)
        try:
            s.connect((BRIDGE, PORTA_PROVA))
            esito["ok"], esito["ms"] = True, round((time.time() - t0) * 1000, 2)
        except Exception as e:
            esito["errore"] = type(e).__name__ + (": " + str(e) if str(e) else "")
        finally:
            s.close()
        with prova_lock:
            prova.update(esito)
        fine.wait(max(0.0, PROVA_OGNI_S - (time.time() - t0)))


# ---------------------------------------------------------------- ciclo principale
def main():
    pidfile = os.path.join(USCITA, "sorveglia.pid")
    if os.path.exists(pidfile):
        try:
            vecchio = int(open(pidfile).read())
            if os.path.exists("/proc/%d" % vecchio) and "sorveglia" in open("/proc/%d/cmdline" % vecchio).read():
                sys.exit("Gia' acceso, PID %d: fermarlo con kill %d" % (vecchio, vecchio))
        except (ValueError, OSError):
            pass
    open(pidfile, "w").write(str(os.getpid()))

    def ferma(*_):
        fine.set()
    signal.signal(signal.SIGTERM, ferma)
    signal.signal(signal.SIGINT, ferma)

    threading.Thread(target=prova_bridge, daemon=True).start()
    registra("avviato, PID %d: bridge %s, porta DAQ %d, prova sulla %d ogni %.1f s, campione ogni %.2f s, "
             "%d s prima e %d s dopo ogni fatto, campioni in %s"
             % (os.getpid(), BRIDGE, PORTA_DAQ, PORTA_PROVA, PROVA_OGNI_S, PASSO_S, ANTE_S, POST_S, USCITA))

    memoria = collections.deque(maxlen=int(ANTE_S / PASSO_S))
    file_campioni, scrivi_fino = None, 0.0
    log_daq = {}          # pid -> {"path", "pos", "readdata", "giving_up"}
    prec = {"daq": [], "mac": None, "rete": None, "retrans": {}, "prova_ok": True}
    ultimo_battito, n_campioni, n_fatti = time.time(), 0, 0
    altri, t_altri = [], 0.0
    prossimo = time.time()

    while not fine.is_set():
        t = time.time()
        fatti = []

        # Tutto /proc una volta al secondo (DAQ nuove, compressione, cloud); le DAQ gia' note a
        # ogni campione, perche' l'istante in cui una run finisce conta quanto quello della caduta.
        if t - t_altri >= 1.0:
            daq, altri = processi()
            t_altri = t
        else:
            daq = [p for p in prec["daq"] if viva(p)]

        # DAQ accese e spente
        for pid in daq:
            if pid not in prec["daq"]:
                registra("DAQ accesa, PID %d" % pid)
        for pid in prec["daq"]:
            if pid not in daq:
                info = log_daq.pop(pid, {})
                registra("DAQ spenta, PID %d (%s)" % (pid, os.path.basename(info.get("path") or "log non trovato")))

        # il log della run: si legge solo la parte nuova
        for pid in daq:
            info = log_daq.setdefault(pid, {"path": None, "pos": 0, "readdata": 0, "giving_up": 0})
            if info["path"] is None:
                info["path"] = log_della_daq(pid)
                if info["path"]:
                    registra("DAQ PID %d scrive %s" % (pid, os.path.basename(info["path"])))
            if info["path"]:
                try:
                    with open(info["path"], errors="replace") as f:
                        f.seek(info["pos"])
                        nuovo = f.read()
                        info["pos"] = f.tell()
                except OSError:
                    nuovo = ""
                n_rd, n_gu = nuovo.count("ReadData failed ("), nuovo.count("giving up")
                if n_rd and info["readdata"] == 0:
                    fatti.append("primo ReadData fallito nella run %s" % os.path.basename(info["path"]))
                if n_gu:
                    fatti.append("la DAQ si arrende (giving up): %s" % os.path.basename(info["path"]))
                info["readdata"] += n_rd
                info["giving_up"] += n_gu

        conn = connessioni()
        with prova_lock:
            pr = dict(prova)
        mac = arp_bridge()
        rete = scheda_rete()
        campione = {"t": round(t, 3), "daq": daq, "conn": conn, "prova": pr, "arp": mac, "rete": rete,
                    "tcp": contatori_tcp(), "altri": altri, "carico": round(os.getloadavg()[0], 2),
                    "log": {str(p): {"readdata": i["readdata"], "giving_up": i["giving_up"]} for p, i in log_daq.items()}}
        n_campioni += 1

        # ritrasmissioni sulle connessioni della DAQ: retrans e' "in corso/totali". Si segnala
        # solo quando il totale cresce: durante una caduta il kernel ritrasmette a intervalli
        # che raddoppiano, quindi poche righe, non una ogni campione.
        retrans = {}
        for c in conn:
            r = c.get("retrans")
            if isinstance(r, str) and "/" in r:
                tot = int(r.split("/")[1])
                chiave = (c.get("pid"), c.get("locale"))
                retrans[chiave] = tot
                if tot > prec["retrans"].get(chiave, 0):
                    fatti.append("ritrasmissioni sulla connessione %s della DAQ (%s, stato %s)" % (c.get("locale"), r, c.get("stato")))
        if pr["t"] is not None and not pr["ok"] and prec["prova_ok"]:
            fatti.append("il bridge non accetta connessioni sulla porta %d (%s)" % (PORTA_PROVA, pr["errore"]))
        if pr["t"] is not None and pr["ok"] and not prec["prova_ok"]:
            fatti.append("il bridge risponde di nuovo sulla porta %d" % PORTA_PROVA)
        if mac and prec["mac"] and mac.split()[0] != prec["mac"].split()[0]:
            fatti.append("il MAC del bridge e' cambiato: %s -> %s" % (prec["mac"], mac))
        if prec["rete"]:
            for k in ["carrier", "oper"] + CONTATORI_RETE:
                if rete.get(k) != prec["rete"].get(k):
                    fatti.append("scheda di rete %s: %s %s -> %s" % (SCHEDA_RETE, k, prec["rete"].get(k), rete.get(k)))

        memoria.append(campione)
        if fatti:
            n_fatti += len(fatti)
            for f in fatti:
                registra(f)
            scrivi_fino = t + POST_S
            if file_campioni is None:
                nome = os.path.join(USCITA, "campioni-%s.jsonl" % time.strftime("%Y%m%d-%H%M%S"))
                file_campioni = open(nome, "w")
                for c in memoria:
                    file_campioni.write(json.dumps(c) + "\n")
                registra("scrivo i %d s prima e i %d s dopo in %s" % (ANTE_S, POST_S, os.path.basename(nome)))
            else:
                file_campioni.write(json.dumps(campione) + "\n")
        elif file_campioni is not None:
            file_campioni.write(json.dumps(campione) + "\n")
            if t > scrivi_fino:
                file_campioni.close()
                file_campioni = None
                registra("fine della scrittura dei campioni")

        if t - ultimo_battito >= BATTITO_S:
            ultimo_battito = t
            registra("vivo: %d campioni, %d fatti; DAQ %s, %d connessioni, bridge %s"
                     % (n_campioni, n_fatti, daq or "spenta", len(conn),
                        "risponde in %.1f ms" % pr["ms"] if pr["ok"] else "NON risponde"))

        prec.update(daq=daq, mac=mac or prec["mac"], rete=rete, retrans=retrans,
                    prova_ok=pr["ok"] if pr["t"] is not None else prec["prova_ok"])
        prossimo += PASSO_S
        if prossimo < time.time():          # rimasto indietro: si riparte da adesso
            prossimo = time.time()
        fine.wait(max(0.0, prossimo - time.time()))

    if file_campioni is not None:
        file_campioni.close()
    registra("fermato")
    try:
        os.remove(pidfile)
    except OSError:
        pass


if __name__ == "__main__":
    main()
