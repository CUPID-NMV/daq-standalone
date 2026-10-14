"""
Le notifiche via mail del controllore: chi le riceve, quando partono, e la
diagnosi di come e' finita una run.

Tre tipi, e ognuno ha la sua lista di persone:

    riepilogo   le run della giornata, una mail all'ora scelta
    errori      una run finita male, con il motivo letto dal suo log
    hw          un guasto dell'hardware: alimentatori, VME, collegamento
                col digitizer

La lista e le impostazioni stanno in data/notifiche.json, che si cambia dalla
pagina; quello che e' partito (o non e' partito) in data/notifiche-storico.jsonl.
Le mail partono in un thread: il server di posta fa greylisting e un invio puo'
durare minuti (tools/posta.py), e la pagina non deve aspettarlo.
"""
import json
import os
import re
import socket
import threading
import time

import posta
import riepilogo_giorno

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIPI = ("riepilogo", "errori", "hw")
NOMI_TIPI = {"riepilogo": "Daily summary", "errori": "Run error", "hw": "Hardware"}
RE_INDIRIZZO = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

PREDEFINITO = {
    "destinatari": [],          # [{"indirizzo": ..., "riepilogo": bool, "errori": bool, "hw": bool}]
    "ora_riepilogo": "20:00",
    "salta_vuoti": True,        # niente riepilogo nei giorni senza run
    "ripeti_hw_min": 30,        # lo stesso allarme hardware al piu' ogni N minuti
    "ultimo_riepilogo": "",     # giorno dell'ultimo riepilogo spedito
}


def indirizzo_pagina(porta=8766):
    """L'indirizzo del controllore come lo si raggiunge dalla rete del banco.

    Calcolato a ogni mail e non scritto nel codice: il PC DAQ prende
    l'indirizzo da DHCP, e il 2026-10-05 e' passato da .108 a .104.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.99.1", 9))      # UDP: non manda niente
        ip = s.getsockname()[0]
        s.close()
    except OSError:
        ip = socket.gethostname()
    return "http://%s:%d/" % (ip, porta)


class Notifiche:
    def __init__(self, config=None, cartelle_dati=None, porta=8766, log=print):
        self.config_path = config or os.path.join(ROOT, "data", "notifiche.json")
        self.storico_path = os.path.splitext(self.config_path)[0] + "-storico.jsonl"
        # Le cartelle in cui cercare le run per il riepilogo: una funzione e
        # non una lista, perche' OutputDir si cambia dalla pagina a caldo.
        self.cartelle_dati = cartelle_dati or (lambda: [os.path.join(ROOT, "data")])
        self.porta = porta
        self.log = log
        self.lock = threading.Lock()
        self.ultimi_hw = {}            # chiave -> istante dell'ultimo invio
        self.cfg = self._carica()
        threading.Thread(target=self._pianificatore, daemon=True).start()

    # -- configurazione ------------------------------------------------------
    def _carica(self):
        cfg = dict(PREDEFINITO)
        try:
            with open(self.config_path) as f:
                cfg.update(json.load(f))
        except (OSError, ValueError):
            pass
        return cfg

    def _salva(self):
        tmp = self.config_path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(tmp, "w") as f:
                json.dump(self.cfg, f, indent=1)
            os.replace(tmp, self.config_path)
        except OSError as e:
            self.log("notifiche: non riesco a salvare %s: %s" % (self.config_path, e))

    def aggiungi(self, indirizzo, tipi=TIPI):
        indirizzo = indirizzo.strip()
        if not RE_INDIRIZZO.match(indirizzo):
            return False, "'%s' is not a mail address." % indirizzo
        if not posta.raggiungibile(indirizzo):
            return False, ("Without SMTP credentials on the DAQ PC only @lnf.infn.it addresses "
                           "can be reached: the LNF incoming servers deliver only to LNF "
                           "mailboxes. See the ? of this panel.")
        with self.lock:
            if any(d["indirizzo"].lower() == indirizzo.lower() for d in self.cfg["destinatari"]):
                return False, "%s is already in the list." % indirizzo
            voce = {"indirizzo": indirizzo}
            voce.update({t: t in tipi for t in TIPI})
            self.cfg["destinatari"].append(voce)
            self._salva()
        return True, "%s added." % indirizzo

    def rimuovi(self, indirizzo):
        with self.lock:
            prima = len(self.cfg["destinatari"])
            self.cfg["destinatari"] = [d for d in self.cfg["destinatari"]
                                       if d["indirizzo"].lower() != indirizzo.lower()]
            if len(self.cfg["destinatari"]) == prima:
                return False, "%s is not in the list." % indirizzo
            self._salva()
        return True, "%s removed." % indirizzo

    def imposta(self, indirizzo, tipo, valore):
        if tipo not in TIPI:
            return False, "Unknown notification type '%s'." % tipo
        with self.lock:
            for d in self.cfg["destinatari"]:
                if d["indirizzo"].lower() == indirizzo.lower():
                    d[tipo] = bool(valore)
                    self._salva()
                    return True, "%s: %s %s." % (indirizzo, NOMI_TIPI[tipo].lower(),
                                                 "on" if valore else "off")
        return False, "%s is not in the list." % indirizzo

    def impostazioni(self, ora=None, salta_vuoti=None, ripeti_hw_min=None):
        with self.lock:
            if ora is not None:
                if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", ora):
                    return False, "Time must be HH:MM, e.g. 20:00."
                self.cfg["ora_riepilogo"] = ora
            if salta_vuoti is not None:
                self.cfg["salta_vuoti"] = bool(salta_vuoti)
            if ripeti_hw_min is not None:
                if not 1 <= ripeti_hw_min <= 1440:
                    return False, "Repeat interval must be 1-1440 minutes."
                self.cfg["ripeti_hw_min"] = int(ripeti_hw_min)
            self._salva()
        return True, "Notification settings saved."

    def destinatari(self, tipi):
        """Chi riceve almeno uno dei tipi dati, senza doppioni."""
        return [d["indirizzo"] for d in self.cfg["destinatari"]
                if any(d.get(t) for t in tipi)]

    # -- storico -------------------------------------------------------------
    def _annota(self, voce):
        try:
            with open(self.storico_path, "a") as f:
                f.write(json.dumps(voce) + "\n")
        except OSError:
            pass

    def storico(self, n=8):
        try:
            with open(self.storico_path) as f:
                righe = f.readlines()[-n:]
            return [json.loads(r) for r in righe if r.strip()]
        except (OSError, ValueError):
            return []

    def stato(self):
        st = self.storico(8)
        # Lo stato dell'invio e' quello dell'ULTIMA mail tentata: se l'ultima
        # non e' partita la pagina deve dirlo in rosso, anche se ieri andava.
        tentate = [v for v in st if v.get("esito") in ("ok", "ko")]
        return {"destinatari": self.cfg["destinatari"],
                "ora_riepilogo": self.cfg["ora_riepilogo"],
                "salta_vuoti": self.cfg["salta_vuoti"],
                "ripeti_hw_min": self.cfg["ripeti_hw_min"],
                "storico": st, "ultima": tentate[-1] if tentate else None,
                "via": posta.via()}

    # -- invio ---------------------------------------------------------------
    def manda(self, tipi, oggetto, testo, html=None, immagini=(), chiave=None, a=None):
        """Spedisce in un thread. `chiave` raggruppa gli allarmi hardware uguali.

        Ritorna subito. Lo storico riceve una voce "in corso" ora e quella
        definitiva quando il server ha risposto.
        """
        tipo = tipi[0]
        destinatari = a if a is not None else self.destinatari(tipi)
        voce = {"quando": time.strftime("%Y-%m-%d %H:%M:%S"), "tipo": tipo,
                "oggetto": oggetto}
        if not destinatari:
            voce.update(esito="nessuno", dettaglio="nobody subscribed")
            self._annota(voce)
            return
        if chiave:
            # Un alimentatore che scatta in continuazione non deve mandare
            # cento mail: la prima basta a far muovere qualcuno, le altre
            # nasconderebbero quelle diverse.
            ora = time.time()
            prima = self.ultimi_hw.get(chiave)
            if prima and ora - prima < 60 * self.cfg["ripeti_hw_min"]:
                voce.update(esito="trattenuta",
                            dettaglio="same alarm sent %.0f min ago" % ((ora - prima) / 60))
                self._annota(voce)
                return
            self.ultimi_hw[chiave] = ora

        def lavoro():
            try:
                m = posta.componi(oggetto, testo, destinatari, html=html, immagini=immagini)
                ok, ko = posta.invia(m, destinatari, log=lambda s: self.log("notifiche: " + s))
            except Exception as e:          # un invio rotto non deve rompere il controllore
                ok, ko = [], {d: str(e) for d in destinatari}
            v = dict(voce, finito=time.strftime("%H:%M:%S"))
            if ko:
                v.update(esito="ko", dettaglio="; ".join("%s: %s" % x for x in ko.items()),
                         consegnate=len(ok))
            else:
                v.update(esito="ok", consegnate=len(ok))
            self._annota(v)
            self.log("notifiche: '%s' -> %d consegnate, %d no" % (oggetto, len(ok), len(ko)))

        threading.Thread(target=lavoro, daemon=True).start()

    def prova(self, indirizzo=None):
        a = [indirizzo] if indirizzo else [d["indirizzo"] for d in self.cfg["destinatari"]]
        if not a:
            return False, "No recipients to send a test to."
        ora = time.strftime("%Y-%m-%d %H:%M:%S %Z")
        self.manda(("prova",), "[DAQ V1742] Test mail - sent %s" % ora,
                   "Test mail from the DAQ controller.\n\nSent at: %s\nController: %s\n"
                   % (ora, indirizzo_pagina(self.porta)), a=a)
        return True, "Test mail queued for %s: the result appears under Last notifications." % ", ".join(a)

    # -- riepilogo giornaliero -----------------------------------------------
    def riepilogo(self, giorno=None, a=None):
        giorno = giorno or time.strftime("%Y-%m-%d")
        oggetto, testo, html_, png, n_run = riepilogo_giorno.componi_riepilogo(
            self.cartelle_dati(), giorno)
        # Gli allarmi della giornata, dallo storico: chi legge solo il
        # riepilogo deve sapere che ce ne sono stati, anche se non li riceve.
        allarmi = [v for v in self.storico(500)
                   if v.get("quando", "").startswith(giorno) and v.get("tipo") in ("errori", "hw")
                   and v.get("esito") in ("ok", "ko", "trattenuta", "nessuno")]
        if allarmi:
            elenco = ["%s %s" % (v["quando"][11:16], v["oggetto"].replace("[DAQ V1742] ", ""))
                      for v in allarmi]
            testo += "\nAlarms today: %d\n  %s\n" % (len(allarmi), "\n  ".join(elenco))
            html_ = html_.replace("</body>", "<p><b>Alarms today: %d</b><br>%s</p></body>" % (
                len(allarmi), "<br>".join(riepilogo_giorno.html.escape(e) for e in elenco)))
        else:
            testo += "\nAlarms today: none\n"
            html_ = html_.replace("</body>", "<p>Alarms today: none</p></body>")
        testo += "\nController: %s\n" % indirizzo_pagina(self.porta)
        self.manda(("riepilogo",), oggetto, testo, html=html_,
                   immagini=[("rate", png)], a=a)
        return n_run

    def _pianificatore(self):
        """Il riepilogo all'ora scelta, una volta al giorno.

        Il giorno dell'ultimo invio sta su file: un controllore riavviato alle
        21 non deve rimandare quello delle 20, e uno rimasto spento alle 20
        lo manda appena riparte, la sera stessa.
        """
        while True:
            try:
                oggi = time.strftime("%Y-%m-%d")
                adesso = time.strftime("%H:%M")
                if adesso >= self.cfg["ora_riepilogo"] and self.cfg["ultimo_riepilogo"] != oggi:
                    with self.lock:
                        self.cfg["ultimo_riepilogo"] = oggi
                        self._salva()
                    if self.destinatari(("riepilogo",)):
                        righe, _ = riepilogo_giorno.run_del_giorno(self.cartelle_dati(), oggi)
                        if righe or not self.cfg["salta_vuoti"]:
                            self.riepilogo(oggi)
                        else:
                            self._annota({"quando": time.strftime("%Y-%m-%d %H:%M:%S"),
                                          "tipo": "riepilogo", "oggetto": "Daily summary %s" % oggi,
                                          "esito": "nessuno", "dettaglio": "no runs today"})
            except Exception as e:
                self.log("notifiche: pianificatore: %s" % e)
            time.sleep(30)


# ---------------------------------------------------------------------------
#  Come e' finita una run
# ---------------------------------------------------------------------------
#  Il codice di uscita della DAQ NON lo dice: se il collegamento col digitizer
#  cade, il C++ scrive "giving up", poi chiude il file con il riepilogo normale
#  ed esce con 0. Si legge il log della run.

# Parole che dicono che il guasto e' dell'hardware, non della run: allora la
# mail va anche a chi segue l'hardware.
SEGNI_HW = ("giving up", "link to the board", "Cannot connect to the digitizer",
            "VME", "bridge", "CAENVME", "Cannot reset digitizer", "ReadData failed",
            "Start acquisition failed")


def esito_run(testo_log, console=(), fermata_a_mano=False, codice=None):
    """(pulita, tipo, motivo). `tipo` e' "errori" o "hw"; `motivo` una frase.

    `testo_log` e' il log della run (puo' essere vuoto, se la DAQ e' morta
    prima di aprirlo); `console` le ultime righe dell'uscita della DAQ, dove
    finiscono i messaggi scritti prima che il log della run esista.
    """
    righe = testo_log.splitlines()
    errori = [r.split(":", 1)[1].strip() for r in righe if r.startswith("Error")]
    errori += [r.strip() for r in console
               if ("Error" in r or "terminate" in r or "what()" in r) and r.strip() not in errori]
    completa = "Acquisition complete." in testo_log
    chiesta = "Arresto richiesto" in testo_log
    tipo_di = lambda testo: "hw" if any(s.lower() in testo.lower() for s in SEGNI_HW) else "errori"

    if completa:
        perso = [e for e in errori if "giving up" in e]
        if perso:
            return False, "hw", perso[0]
        if chiesta:
            return True, "", "stopped on request"
        m_ev = re.search(r"Events written to file:\s*(\d+)", testo_log)
        m_n = re.search(r"NEvents\s*=\s*(\d+)", testo_log)
        if m_ev and m_n and int(m_ev.group(1)) < int(m_n.group(1)):
            # L'unica uscita dal ciclo che non scrive niente: ~83 minuti senza
            # un solo evento (maxRetries in AcquireEvents).
            return False, "errori", ("ended after %s of %s events with nobody asking: "
                                     "no trigger arrived for a long time (the DAQ gives up "
                                     "after ~83 min without events)" % (m_ev.group(1), m_n.group(1)))
        if errori:
            return False, tipo_di(errori[0]), "completed, but with errors: " + errori[0]
        return True, "", "reached the requested number of events"

    # Senza il riepilogo finale il file non e' stato chiuso.
    if errori:
        return False, tipo_di(" ".join(errori)), errori[-1]
    if codice is not None and codice < 0:
        nome = {-9: "SIGKILL", -6: "SIGABRT (crash)", -11: "SIGSEGV (crash)",
                -15: "SIGTERM"}.get(codice, "signal %d" % -codice)
        return False, "errori", "the DAQ process was killed by %s, before closing the file" % nome
    if fermata_a_mano:
        return False, "errori", ("stopped by hand, but the DAQ did not close the file: "
                                 "it was killed before writing the end-of-run summary")
    return False, "errori", ("the DAQ process ended without the end-of-run summary and "
                             "without an error message: killed or crashed%s"
                             % ("" if codice is None else " (exit code %d)" % codice))
