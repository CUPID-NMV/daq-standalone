"""
Le notifiche via mail del controllore: chi le riceve, quando partono, e la
diagnosi di come e' finita una run.

Cinque tipi, e ognuno ha la sua lista di persone:

    riepilogo   le run della giornata, una mail all'ora scelta
    sequenza    le run di una coda, quando la coda finisce (non gli scan)
    errori      una run finita male, con il motivo letto dal suo log
    hw          un guasto dell'hardware: alimentatori, VME, collegamento
                col digitizer
    cloud       una run di dati che non e' sul cloud mezz'ora dopo la fine,
                qualunque sia il motivo; un promemoria ogni 3 ore finche'
                dura, una mail quando e' risolto

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

PREFISSO = posta.PREFISSO_OGGETTO      # l'inizio dell'oggetto delle mail: vedi posta.py

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIPI = ("riepilogo", "sequenza", "errori", "hw", "cloud")
NOMI_TIPI = {"riepilogo": "Daily summary", "sequenza": "Sequence end",
             "errori": "Run error", "hw": "Hardware", "cloud": "Cloud upload"}
RE_INDIRIZZO = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

PREDEFINITO = {
    "destinatari": [],          # [{"indirizzo": ..., "riepilogo": bool, "errori": bool, "hw": bool}]
    "ora_riepilogo": "20:00",
    "salta_vuoti": True,        # niente riepilogo nei giorni senza run
    "ripeti_hw_min": 30,        # lo stesso allarme hardware al piu' ogni N minuti
    "ultimo_riepilogo": "",     # giorno dell'ultimo riepilogo spedito
    # Una run sul cloud ci arriva 3-4 minuti dopo la fine (2 di attesa, 18 s di
    # compressione, 7 s di trasferimento), fino a ~12 se la fine la vede solo
    # il giro dei 10 minuti. Mezz'ora sono almeno due tentativi falliti.
    "cloud_ritardo_min": 30,
    "cloud_ripeti_h": 3,        # promemoria finche' l'allarme dura
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
        # Accanto alla lista e non in data/: un controllore di prova con la sua
        # lista ha anche il suo stato degli allarmi del cloud.
        self.cloud_path = os.path.splitext(self.config_path)[0] + "-cloud.json"
        self.cloud_ultimo = None       # l'ultima situazione letta da archivia.py
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
        # Il tipo "cloud" e' nato il 2026-11-02, dopo la lista: chi riceve gli
        # allarmi dell'hardware riceve anche questi, che sono dello stesso
        # genere -- qualcosa si e' rotto e nessuno se ne accorgerebbe.
        for d in cfg["destinatari"]:
            d.setdefault("cloud", bool(d.get("hw")))
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
            return False, ("Only @lnf.infn.it addresses can be used: from Frascati the mail "
                           "leaves through the LNF incoming servers, which deliver only to LNF "
                           "mailboxes. To get it elsewhere, forward it from an LNF mailbox.")
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
                "storico": st, "ultima": tentate[-1] if tentate else None}

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
        self.manda(("prova",), PREFISSO + " Test mail - sent %s" % ora,
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
                   if v.get("quando", "").startswith(giorno) and v.get("tipo") in ("errori", "hw", "cloud")
                   and v.get("esito") in ("ok", "ko", "trattenuta", "nessuno")]
        # Dove sono le run del giorno: fa anche da segnale di vita. Dal 23/10
        # al 2/11 non e' partita nessuna mail e nessuno se n'e' accorto; un
        # riepilogo che non arriva, con questa riga dentro, si nota.
        nuvola = riepilogo_cloud(self.cloud_ultimo, giorno, 60 * self.cfg["cloud_ritardo_min"])
        testo += "\n" + nuvola + "\n"
        html_ = html_.replace("</body>", "<p>%s</p></body>" % "<br>".join(
            riepilogo_giorno.html.escape(r) for r in nuvola.splitlines()))
        if allarmi:
            elenco = ["%s %s" % (v["quando"][11:16], posta.senza_prefisso(v["oggetto"]))
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

    def fine_sequenza(self, log_run, come, inizio, chi=""):
        """La mail di fine coda, composta e spedita in un thread.

        Comporla vuol dire stimare l'efficienza dell'ultima run, che di solito
        non e' ancora in cache: una ventina di secondi che il controllore non
        deve aspettare.
        """
        if not self.destinatari(("sequenza",)):
            return

        def lavoro():
            try:
                allarmi = []
                da = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(inizio or 0))
                for v in self.storico(500):
                    if v.get("tipo") in ("errori", "hw", "cloud") and v.get("quando", "") >= da:
                        allarmi.append("%s %s" % (v["quando"][11:16],
                                                  posta.senza_prefisso(v["oggetto"])))
                oggetto, testo, html_, png = riepilogo_giorno.componi_sequenza(
                    log_run, come, inizio, chi, allarmi)
                testo += "\nController: %s\n" % indirizzo_pagina(self.porta)
                self.manda(("sequenza",), oggetto, testo, html=html_,
                           immagini=[("rate", png)] if png else ())
            except Exception as e:
                self.log("notifiche: fine sequenza: %s" % e)
                self._annota({"quando": time.strftime("%Y-%m-%d %H:%M:%S"), "tipo": "sequenza",
                              "oggetto": "Sequence end", "esito": "ko",
                              "dettaglio": "could not compose: %s" % e})

        threading.Thread(target=lavoro, daemon=True).start()

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

    # -- cloud ---------------------------------------------------------------
    def controlla_cloud(self, st):
        """Gli allarmi del cloud. `st` e' la situazione di tools/archivia.py
        --stato --json, o {"errore": ...} se non si e' potuta leggere; la passa
        il controllore ogni due minuti.

        Una mail quando una run comincia a essere in ritardo, un promemoria
        ogni cloud_ripeti_h ore finche' ce n'e' una, una quando sono arrivate;
        le run che sul cloud non andranno (chiuse male) una volta sola. Lo
        stato sta su file: un controllore riavviato non rimanda un allarme gia'
        mandato, e uno rimasto spento lo manda appena riparte.
        """
        adesso = time.time()
        self.cloud_ultimo = st
        ritardo = 60 * self.cfg["cloud_ritardo_min"]
        s = self._leggi_cloud()
        prima = json.dumps(s, sort_keys=True)
        s.setdefault("attivo_dal", adesso)
        s.setdefault("avvisate", [])
        problemi = []
        if "errore" in st:
            # Una situazione che non si legge e' un guasto anche lei: se dura,
            # l'allarme che si aspetta non arriverebbe mai.
            # Finche' e' breve non si decide niente, nemmeno che un allarme in
            # corso sia finito: delle run, in quel momento, non si sa nulla.
            s.setdefault("errore_dal", adesso)
            if adesso - s["errore_dal"] <= ritardo:
                return self._salva_cloud(s, prima)
            problemi = [{"nome": STATO_ILLEGGIBILE, "fine": s["errore_dal"],
                         "riga": "the upload status cannot be read since %s: %s"
                                 % (_giorno_ora(s["errore_dal"]), st["errore"][-300:])}]
        else:
            s.pop("errore_dal", None)
            if not st.get("cloud"):
                # Upload spento per scelta nel TOML: lo dice la pagina, niente mail.
                return self._salva_cloud(s, prima)
            problemi = [{"nome": v["nome"], "fine": v["fine"], "riga": riga_cloud(v, adesso)}
                        for v in st.get("run", [])
                        if v["stato"] in DA_ARRIVARE and adesso - v["fine"] > ritardo]
            # Solo quelle finite dopo l'attivazione di questo controllo: alla
            # prima accensione non deve arrivare l'elenco di tutto il passato.
            nuove = [v for v in st.get("run", []) if v["stato"] in SOLO_DISCO
                     and v["nome"] not in s["avvisate"] and v["fine"] > s["attivo_dal"]]
            if nuove:
                s["avvisate"] = (s["avvisate"] + [v["nome"] for v in nuove])[-500:]
                self.manda(("cloud",), PREFISSO + " CLOUD — %s only on the DAQ disk" % (
                    "run %s stays" % nuove[0]["nome"] if len(nuove) == 1 else "%d runs stay" % len(nuove)),
                    "These runs were closed badly -- the DAQ died before closing the file -- so\n"
                    "archivia.py neither compresses nor sends them: such a file can only be read\n"
                    "in live (SWMR) mode. They stay as .h5 on the DAQ disk.\n\n  - %s\n\n"
                    "If one is needed on the cloud, on the DAQ PC:\n"
                    "  python3 tools/archivia.py --aggiungi FILE\n\nController: %s\n"
                    % ("\n  - ".join(riga_cloud(v, adesso) for v in nuove),
                       indirizzo_pagina(self.porta)))

        nomi = [p["nome"] for p in problemi]
        if problemi and not s.get("allarme_dal"):
            s.update(allarme_dal=adesso, ultima_mail=adesso, in_allarme=nomi)
            self._mail_cloud(problemi, st, adesso, ancora=False)
        elif problemi:
            s["in_allarme"] = list(dict.fromkeys(s.get("in_allarme", []) + nomi))
            if adesso - s.get("ultima_mail", 0) >= 3600 * self.cfg["cloud_ripeti_h"]:
                s["ultima_mail"] = adesso
                self._mail_cloud(problemi, st, adesso, ancora=True)
        elif s.get("allarme_dal"):
            per_nome = {v["nome"]: v for v in st.get("run", [])}
            righe = []
            for n in s.get("in_allarme", []):
                v = per_nome.get(n)
                if n == STATO_ILLEGGIBILE:
                    righe.append("the upload status can be read again")
                elif v and v["stato"] == "sul cloud":
                    righe.append("%s: on the cloud since %s" % (n, _giorno_ora(v["quando"])))
                elif v:
                    righe.append("%s: %s" % (n, riga_cloud(v, adesso)))
                else:
                    righe.append("%s: no longer among the runs to send (moved or deleted?)" % n)
            self.manda(("cloud",), PREFISSO + " CLOUD OK — %s" % (
                "run %s is on the cloud" % s["in_allarme"][0] if len(s.get("in_allarme", [])) == 1
                and s["in_allarme"][0] != STATO_ILLEGGIBILE else "the alarm is over"),
                "The cloud alarm that started at %s is over, after %s.\n\n  - %s\n\nController: %s\n"
                % (_giorno_ora(s["allarme_dal"]), _durata(adesso - s["allarme_dal"]),
                   "\n  - ".join(righe), indirizzo_pagina(self.porta)))
            s.pop("allarme_dal", None)
            s.pop("in_allarme", None)
        self._salva_cloud(s, prima)

    def _mail_cloud(self, problemi, st, adesso, ancora):
        n = len(problemi)
        if n == 1 and problemi[0]["nome"] == STATO_ILLEGGIBILE:
            oggetto = "the upload status cannot be read"
        elif n == 1:
            oggetto = "run %s not on the cloud for %s" % (problemi[0]["nome"],
                                                        _durata(adesso - problemi[0]["fine"]))
        else:
            oggetto = "%d runs not on the cloud, the oldest for %s" % (
                n, _durata(adesso - min(p["fine"] for p in problemi)))
        ok, ko = st.get("ultimo_caricato"), st.get("ultimo_errore")
        corpo = ("%s\n\n  - %s\n\n"
                 "Nothing is lost: the files stay on the DAQ disk. A failed transfer is tried\n"
                 "again after 5, 15 and 45 min, then every few hours, and all of them as soon\n"
                 "as one transfer goes through; a name conflict or a file changed after\n"
                 "sending needs someone.\n"
                 "%s%s\n"
                 "Details, on the DAQ PC:  python3 tools/archivia.py --stato\n"
                 "A reminder follows every %d h while this lasts, and a mail when it is over.\n"
                 "Controller: %s\n"
                 % ("These runs should be on the cloud %d min after their end, and are not:"
                    % self.cfg["cloud_ritardo_min"],
                    "\n  - ".join(p["riga"] for p in problemi),
                    "Last transfer that went through: %s, %s.\n" % (_giorno_ora(ok["quando"]), ok["chiave"])
                    if ok else "",
                    "Last transfer error: %s, %s: %s\n" % (_giorno_ora(ko["quando"]), ko["chiave"],
                                                           breve_errore(ko["messaggio"])) if ko else "",
                    self.cfg["cloud_ripeti_h"], indirizzo_pagina(self.porta)))
        self.manda(("cloud",), PREFISSO + " CLOUD — %s%s" % ("still: " if ancora else "", oggetto), corpo)

    def _leggi_cloud(self):
        try:
            with open(self.cloud_path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _salva_cloud(self, s, prima):
        if json.dumps(s, sort_keys=True) == prima:
            return
        tmp = self.cloud_path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(s, f, indent=1)
            os.replace(tmp, self.cloud_path)
        except OSError as e:
            self.log("notifiche: non riesco a salvare %s: %s" % (self.cloud_path, e))


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


# ---------------------------------------------------------------------------
#  Le run sul cloud
# ---------------------------------------------------------------------------
#  Dove sono le run lo dice tools/archivia.py --stato --json; qui si decide
#  cosa e' in ritardo e come dirlo. Si guarda alle RUN, non ai tentativi: una
#  run che non c'e' e' un problema anche se nessuno ha provato a mandarla
#  (controllore spento, compressione fallita), e un tentativo fallito da solo
#  non lo e' -- il 2026-11-02 alle 09:54 il server dei token di CNAF non ha
#  risposto una volta, e il tentativo dopo e' passato.

# Una run in questi stati deve ancora arrivare: oltre il ritardo, allarme.
DA_ARRIVARE = ("da comprimere", "compressione fallita", "da mandare", "in pausa",
               "conflitto", "cambiato")
# Queste sul cloud non andranno: si dice una volta sola.
SOLO_DISCO = ("chiusa male", "non HDF5")
STATO_ILLEGGIBILE = "(upload status)"
RE_NUMERO_RUN = re.compile(r"_(\d{4})(?:_|$)")


def _hm(t):
    return time.strftime("%H:%M", time.localtime(t))


def _giorno_ora(t):
    return time.strftime("%d/%m %H:%M", time.localtime(t))


def _durata(s):
    s = max(0, int(s))
    return "%d min" % (s // 60) if s < 5400 else "%d h %02d min" % (s // 3600, s % 3600 // 60)


def breve_errore(msg, n=220):
    """L'errore di cp_data in una riga che si legge: l'ultima riga del suo
    stderr e' un'eccezione Python lunga 300 caratteri, con il motivo vero fra
    apici in fondo ("Connection to iam-cygno.cloud.cnaf.infn.it timed out")."""
    m = re.match(r"^\s*([\w.]+)\s*:", msg or "")
    classe = m.group(1).rsplit(".", 1)[-1] if m else ""
    if not any(k in classe for k in ("Error", "Timeout", "Exception")):
        classe = ""
    citati = re.findall(r"'([^']{12,})'", msg or "")
    if classe and citati:
        return ("%s: %s" % (classe, citati[-1]))[:n]
    msg = (msg or "").strip()
    return msg if len(msg) <= n else msg[:n // 2 - 2] + " \u2026 " + msg[-(n // 2 - 2):]


def riga_cloud(v, adesso=None):
    """Una run che non e' sul cloud, in una riga: dove e' rimasta e perche'."""
    adesso = adesso or time.time()
    st, n = v["stato"], v.get("tentativi", 0)
    if st == "da comprimere":
        perche = "not compressed yet"
    elif st == "compressione fallita":
        perche = "compression failed at %s: %s" % (_hm(v["quando_errore"]), breve_errore(v["errore"]))
    elif st == "conflitto":
        perche = ("NOT sent: a different file with the same name is already on the cloud (%s)"
                  % v.get("altro", "?"))
    elif st == "cambiato":
        perche = "changed on disk after it was sent: not sent again by itself"
    elif st == "chiusa male":
        perche = "closed badly (the DAQ died before closing it): it stays only on the DAQ disk"
    elif st == "non HDF5":
        perche = "not an HDF5 file: neither compressed nor sent"
    elif st == "in corso":
        perche = "still running"
    elif n:
        perche = "transfer failed %d time%s, last at %s: %s" % (
            n, "" if n == 1 else "s", _hm(v["quando_errore"]), breve_errore(v["errore"]))
        perche += ("; next attempt after %s" % _hm(v["prossimo"]) if v.get("prossimo")
                   else "; tried again at the next round")
    else:
        perche = "never tried: archivia.py has not run since (is the controller running?)"
    return "%s, ended %s (%s ago): %s" % (v["nome"], _giorno_ora(v["fine"]),
                                         _durata(adesso - v["fine"]), perche)


def riepilogo_cloud(st, giorno, ritardo_s):
    """Le righe del riepilogo serale: quante run del giorno sono sul cloud."""
    if not st:
        return "Cloud: not checked yet (the controller has just started)."
    if "errore" in st:
        return "Cloud: unknown, the upload status cannot be read: %s" % st["errore"][-300:]
    if not st.get("cloud"):
        return "Cloud: upload off (%s)." % st.get("motivo_spento", "")
    adesso = st.get("adesso") or time.time()
    giorno_di = lambda v: time.strftime("%Y-%m-%d", time.localtime(v["fine"]))
    del_giorno = [v for v in st.get("run", []) if giorno_di(v) == giorno and v["stato"] != "in corso"]
    sul = [v for v in del_giorno if v["stato"] == "sul cloud"]
    if not del_giorno:
        righe = ["Cloud: no runs of the day to send."]
    else:
        righe = ["Cloud: %d of %d runs of the day are on the cloud%s." % (
            len(sul), len(del_giorno),
            ", the last one sent at %s" % _hm(max(v["quando"] for v in sul)) if sul else "")]
    fuori = [v for v in del_giorno if v["stato"] != "sul cloud"]
    prima = [v for v in st.get("run", []) if giorno_di(v) < giorno and v["stato"] in DA_ARRIVARE]
    if fuori or prima:
        righe.append("Not on the cloud:")
    for v in prima + fuori:
        if v["stato"] in DA_ARRIVARE and adesso - v["fine"] < ritardo_s:
            righe.append("  on its way: %s, ended %s" % (v["nome"], _hm(v["fine"])))
        else:
            righe.append("  " + riga_cloud(v, adesso))
    return "\n".join(righe)


def pagina_cloud(st, ritardo_s):
    """Cosa dice la barra in alto della pagina di controllo: livello (bene,
    attenzione, male, spento), testo breve, e le righe per chi ci passa sopra."""
    if not st:
        return {"livello": "spento", "testo": "CLOUD: NOT CHECKED YET", "righe": [], "problemi": []}
    if "errore" in st:
        return {"livello": "attenzione", "testo": "CLOUD: STATUS UNKNOWN",
                "righe": ["the upload status cannot be read: " + st["errore"][-300:]], "problemi": []}
    if not st.get("cloud"):
        return {"livello": "spento", "testo": "CLOUD UPLOAD OFF",
                "righe": [st.get("motivo_spento", "")], "problemi": []}
    adesso = st.get("adesso") or time.time()
    run = st.get("run", [])
    tardi = [v for v in run if v["stato"] in DA_ARRIVARE and adesso - v["fine"] > ritardo_s]
    in_viaggio = [v for v in run if v["stato"] in DA_ARRIVARE and v not in tardi]
    solo_disco = [v for v in run if v["stato"] in SOLO_DISCO and adesso - v["fine"] < 86400]
    righe = [riga_cloud(v, adesso) for v in tardi]
    righe += ["on its way: %s (%s)" % (v["nome"], riga_cloud(v, adesso).split("ago): ", 1)[-1])
              for v in in_viaggio]
    righe += [riga_cloud(v, adesso) for v in solo_disco]
    ok, ko = st.get("ultimo_caricato"), st.get("ultimo_errore")
    if ok:
        righe.append("last transfer: %s, %s" % (_giorno_ora(ok["quando"]), ok["chiave"]))
    if ko:
        righe.append("last transfer error: %s, %s: %s" % (_giorno_ora(ko["quando"]), ko["chiave"],
                                                          breve_errore(ko["messaggio"])))
    if tardi:
        return {"livello": "male", "righe": righe, "problemi": [riga_cloud(v, adesso) for v in tardi],
                "testo": "⚠ %s NOT ON THE CLOUD · FOR %s" % (
                    "1 RUN" if len(tardi) == 1 else "%d RUNS" % len(tardi),
                    _durata(adesso - tardi[0]["fine"]).upper())}
    if any(v.get("tentativi") for v in in_viaggio):
        return {"livello": "attenzione", "testo": "CLOUD: RETRYING", "righe": righe, "problemi": []}
    sul = [v for v in run if v["stato"] == "sul cloud"]
    if sul:
        v = max(sul, key=lambda v: v["quando"])
        m = RE_NUMERO_RUN.search(v["nome"])
        testo = "CLOUD OK · %s at %s" % ("run " + m.group(1) if m else v["nome"], _hm(v["quando"]))
    else:
        testo = "CLOUD OK"
    return {"livello": "bene", "testo": testo, "righe": righe, "problemi": []}
