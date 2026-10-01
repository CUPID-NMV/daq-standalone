#!/usr/bin/env python3
"""Controllo della DAQ da browser: stato, avvio e arresto.

    python3 tools/daq_control.py                 # solo da questa macchina
    python3 tools/daq_control.py -b 0.0.0.0      # raggiungibile dalla rete

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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BINARIO = os.path.join(ROOT, "build", "main", "DAQ-WC")
AZIONI = os.path.join(ROOT, "data", "azioni.jsonl")

# Quanto si aspetta che la DAQ chiuda dopo SIGTERM prima di dire che non
# risponde. Nelle prove esce in un secondo; trenta sono larghi apposta, perche'
# la chiusura comprime il file e un file grosso ci mette.
ATTESA_ARRESTO_S = 30

# Sequenze di colore con cui la DAQ decora l'uscita.
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


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
        # Il contatore di eventi usa \r per riscrivere la stessa riga: senza
        # convertirlo si vedrebbe una riga sola lunga chilometri. E la DAQ
        # colora l'uscita, quindi vanno tolte le sequenze ANSI: altrimenti
        # finiscono nel <pre> della pagina come caratteri strani, e peggio
        # ancora si attaccano in coda ai valori estratti da qui, tipo il nome
        # del file della run.
        testo = ANSI.sub("", testo)
        righe = [r for r in testo.replace("\r", "\n").split("\n") if r.strip()]
        return righe[-n:]

    def eventi_correnti(self):
        """(decodificati, richiesti, file) dall'ultima riga di avanzamento."""
        eventi = richiesti = None
        runfile = None
        for riga in self.coda_log(400):
            if "Events decoded:" in riga:
                try:
                    a, b = riga.split("Events decoded:")[1].strip().split("/")
                    eventi, richiesti = int(a), int(b)
                except (ValueError, IndexError):
                    pass
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
PAGINA = """<!doctype html>
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
  <div class="box"><h2>Configurazione</h2><table id="cfg"></table>
    <div style="margin-top:10px;font-size:12px;color:#6b6a65">
      In sola lettura: per cambiarla si modifica il TOML. Le soglie del
      self-trigger valgono subito, tutto il resto al prossimo avvio.
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
  "Fermare la run in corso?\\n\\nLa DAQ chiude il file e resetta la board: " +
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

  const c = s.config || {};
  if(c.errore){ tabella($("cfg"), [["errore", c.errore]]); }
  else{
    const f = [["file", (c.file||"").split("/").pop()],
               ["campionamento", c.campionamento],
               ["post-trigger", c.post_trigger + " %"],
               ["canali", JSON.stringify(c.canali)],
               ["eventi richiesti", c.eventi_richiesti],
               ["trigger esterno", c.trigger_esterno ? "acceso" : "spento"],
               ["self-trigger", c.self_trigger
                   ? "acceso, offset " + JSON.stringify(c.self_offset) +
                     " su ch " + JSON.stringify(c.self_canali)
                   : "spento"]];
    if(c.cfd) f.push(["CFD V812", c.cfd.attivo
        ? "acceso, " + JSON.stringify(c.cfd.soglie_mv) + " mV su ch " +
          JSON.stringify(c.cfd.canali)
        : "spento"]);
    f.push(["collegamento", c.collegamento]);
    tabella($("cfg"), f);
  }

  tabella($("run"), s.in_corso
    ? [["pid", s.pid],
       ["file", s.run],
       ["eventi", s.eventi === null ? "—" : s.eventi + " / " + s.eventi_richiesti],
       ["rate (ultimi 30 s)", s.rate === null ? "in attesa" : s.rate + " Hz"],
       ["in corso da", s.da_secondi === null ? "—" : Math.round(s.da_secondi) + " s"]]
    : [["", "nessuna run in corso"]]);

  $("log").textContent = (s.log || []).join("\\n");
  $("azioni").innerHTML = (s.azioni || []).slice().reverse().map(a =>
    `${a.quando} &middot; <b>${a.chi}</b> da ${a.da}: ${a.azione} &rarr; ${a.esito}`
  ).join("<br>") || "nessuna azione registrata";
}

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
            if parti.path == "/api/stato":
                if not self._autorizzato(qs):
                    return self._json({"errore": "token mancante o sbagliato"}, 403)
                return self._json(ctrl.stato())
            self._manda(404, "text/plain", b"not found")

        def do_POST(self):
            parti = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parti.query)
            if not self._autorizzato(qs):
                return self._json({"esito": False, "messaggio": "token mancante o sbagliato"}, 403)

            chi = qs.get("chi", [""])[0].strip()[:40]
            da = self.client_address[0]
            if parti.path == "/api/avvia":
                esito, messaggio = ctrl.avvia()
                registra(chi, da, "avvia", messaggio)
            elif parti.path == "/api/ferma":
                esito, messaggio = ctrl.ferma()
                registra(chi, da, "ferma", messaggio)
            else:
                return self._manda(404, "text/plain", b"not found")
            self._json({"esito": esito, "messaggio": messaggio})

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
    print("Configurazione : %s" % ctrl.toml)
    print("Binario        : %s%s" % (BINARIO, "" if os.path.exists(BINARIO) else "   NON ESISTE"))
    print("Uscita DAQ     : %s" % args.log)
    if args.token:
        print("Token          : attivo")
    pid = trova_daq()
    print("DAQ            : %s" % ("in esecuzione, pid %d" % pid if pid else "ferma"))

    try:
        server = ThreadingHTTPServer((args.bind, args.port), crea_handler(ctrl, args.token))
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        sys.exit("\nLa porta %d e' gia' in uso: c'e' gia' un controllore attivo?\n"
                 "Trova il processo con:  ss -ltnp | grep %d" % (args.port, args.port))

    print("\nPagina su http://%s:%d/" % (args.bind, args.port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nchiuso")


if __name__ == "__main__":
    main()
