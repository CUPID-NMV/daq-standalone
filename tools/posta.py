#!/usr/bin/env python3
"""
Spedizione delle mail della DAQ: riepilogo giornaliero, errori, allarmi.

    python3 tools/posta.py --prova luigi.benussi@lnf.infn.it

La posta esce dai server di posta IN INGRESSO dei Laboratori, pmg1 e pmg2,
porta 25, senza login: consegnano a qualunque casella @lnf.infn.it e
rifiutano tutto il resto ("Relay access denied"). Quindi si scrive solo a
indirizzi LNF; chi vuole le mail altrove se le inoltra dalla sua casella.

E' una scelta, non un limite della rete: smtp.lnf.infn.it, il server di
invio, dal PC DAQ risponde (25/587/465, login dopo STARTTLS, verificato il
2026-10-14), ma usarlo vorrebbe dire una password personale in chiaro su una
macchina condivisa. Se un giorno servira', basta cambiare SERVER e aggiungere
il login: il resto non cambia.

I due server fanno greylisting: la prima volta che vedono una coppia
mittente/destinatario rispondono 450 "try later" e accettano solo chi riprova
qualche minuto dopo. Per questo `invia` RIPROVA da sola sui rifiuti temporanei
invece di dichiarare fallito l'invio -- un allarme perso perche' il server ha
detto "riprova" sarebbe il peggiore dei guasti, perche' non lo vede nessuno.
"""
import argparse
import smtplib
import socket
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

SERVER = ("pmg1.lnf.infn.it", "pmg2.lnf.infn.it")
PORTA = 25
MITTENTE = "DAQ V1742 <daq@lnf.infn.it>"
DOMINI_RAGGIUNGIBILI = ("lnf.infn.it",)

# Il greylisting accetta dopo ~5 minuti (misurato: rifiuto alle 15:20, accettato
# alle 15:26). Si riprova per un quarto d'ora abbondante prima di arrendersi.
ATTESE_S = (60, 120, 240, 480)


def raggiungibile(indirizzo):
    """Vero se l'indirizzo e' in un dominio a cui i server sanno consegnare."""
    dominio = indirizzo.rsplit("@", 1)[-1].lower()
    return any(dominio == d or dominio.endswith("." + d)
               for d in DOMINI_RAGGIUNGIBILI)


def componi(oggetto, testo, destinatari, html=None, immagini=(), rispondi_a=None):
    """La mail. `immagini` e' una lista di (cid, png_bytes) usati dall'HTML.

    Le immagini viaggiano DENTRO il corpo (cid:), non come allegati da aprire:
    un grafico che va scaricato per vederlo non lo guarda nessuno.
    """
    m = EmailMessage()
    m["From"] = MITTENTE
    m["To"] = ", ".join(destinatari)
    if rispondi_a:
        m["Reply-To"] = rispondi_a
    m["Subject"] = oggetto
    m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = make_msgid(domain="lnf.infn.it")
    m.set_content(testo)
    if html:
        m.add_alternative(html, subtype="html")
        parte_html = m.get_payload()[-1]
        for cid, png in immagini:
            parte_html.add_related(png, maintype="image", subtype="png",
                                   cid="<%s>" % cid, filename=cid + ".png")
    return m


def _un_tentativo(m, destinatari):
    """Prova i server in fila. Ritorna (consegnati, rifiutati_temp, rifiutati_def, ultimo_errore)."""
    ultimo = ""
    for host in SERVER:
        try:
            s = smtplib.SMTP(host, PORTA, timeout=30)
            s.ehlo()
            if s.has_extn("starttls"):
                s.starttls()
                s.ehlo()
            try:
                rifiuti = s.send_message(m, to_addrs=destinatari)
            except smtplib.SMTPRecipientsRefused as e:
                rifiuti = e.recipients
            finally:
                try:
                    s.quit()
                except smtplib.SMTPException:
                    pass
            # "Relay access denied" arriva con 454, un codice da rifiuto
            # temporaneo, ma non cambiera' mai: riprovare un quarto d'ora un
            # indirizzo fuori da LNF sarebbe solo tempo perso (2026-10-14).
            definitivo = lambda r: r[0] >= 500 or b"relay access denied" in (
                r[1] if isinstance(r[1], bytes) else str(r[1]).encode()).lower()
            temp = {a: r for a, r in rifiuti.items() if 400 <= r[0] < 500 and not definitivo(r)}
            defi = {a: r for a, r in rifiuti.items() if definitivo(r)}
            ok = [a for a in destinatari if a not in rifiuti]
            return ok, temp, defi, ""
        except (OSError, smtplib.SMTPException) as e:
            # Un server che non risponde non e' un rifiuto: si passa all'altro.
            ultimo = "%s: %s" % (host, e)
    return [], {a: (0, ultimo) for a in destinatari}, {}, ultimo


def invia(m, destinatari, attese=ATTESE_S, log=print):
    """Spedisce, riprovando sui rifiuti temporanei. Ritorna (consegnati, falliti).

    `falliti` e' {indirizzo: motivo}. Blocca per tutta la durata dei tentativi:
    chi non puo' aspettare (il controllore) la chiama in un thread.
    """
    da_fare = [a for a in destinatari if raggiungibile(a)]
    falliti = {a: "dominio non raggiungibile da questa rete (solo @lnf.infn.it)"
               for a in destinatari if not raggiungibile(a)}
    consegnati = []
    for k in range(len(attese) + 1):
        if not da_fare:
            break
        ok, temp, defi, err = _un_tentativo(m, da_fare)
        consegnati += ok
        for a, r in defi.items():
            falliti[a] = "%d %s" % (r[0], r[1].decode(errors="replace") if isinstance(r[1], bytes) else r[1])
        da_fare = list(temp)
        if da_fare and k < len(attese):
            log("rifiuto temporaneo per %s, riprovo fra %d s" % (", ".join(da_fare), attese[k]))
            time.sleep(attese[k])
    for a in da_fare:
        r = temp[a]
        falliti[a] = "ancora rifiutato dopo %d tentativi: %d %s" % (
            len(attese) + 1, r[0], r[1].decode(errors="replace") if isinstance(r[1], bytes) else r[1])
    return consegnati, falliti


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prova", nargs="+", required=True, metavar="INDIRIZZO",
                    help="manda una mail di prova a questi indirizzi")
    a = ap.parse_args()
    ora = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    m = componi("[DAQ V1742] Test mail - sent %s" % ora,
                "Test mail from the DAQ PC (%s).\n\nSent at: %s\n" % (socket.gethostname(), ora),
                a.prova)
    ok, ko = invia(m, a.prova)
    for x in ok:
        print("consegnata a", x)
    for x, motivo in ko.items():
        print("NON consegnata a %s: %s" % (x, motivo))


if __name__ == "__main__":
    main()
