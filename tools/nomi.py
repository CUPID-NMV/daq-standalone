#!/usr/bin/env python3
"""
Come la tensione dei SiPM entra nel nome dei file di una run.

Modulo senza dipendenze, condiviso fra la pagina di controllo e gli script
che avviano la DAQ per conto loro (lo scan del V812). Due copie di questa
regola divergerebbero, ed e' gia' successo con il parser dei canali: il
monitor capiva "12-15" e il controllore no.

La regola e' una sola: **meglio un nome senza tensione che un nome con la
tensione sbagliata**. Per questo l'etichetta si ricalcola sempre da zero, e
quando non si sa si TOGLIE invece di lasciare quella di prima.
"""
import re

# "_54-0V" oppure "_HVoff" in fondo al prefisso. Serve a toglierla prima di
# rimetterla: se no a ogni run il prefisso cresce di un pezzo.
RE_HV = re.compile(r"_(?:\d+-\dV|HVoff)$")

# Oltre questa eta' una lettura dell'alimentatore non dice piu' niente su
# adesso. Tre periodi di lettura, come il riassunto nella pagina.
ETA_MAX_S = 10.0


def etichetta(lettura, adesso=None, eta_max=ETA_MAX_S):
    """L'etichetta per una lettura dell'alimentatore, oppure None.

    Si usa la tensione MISURATA, non quella impostata: nel nome va quello che
    il rivelatore ha visto. Arrotondata a un decimo di volt resta stabile per
    tutta una sequenza -- 54.02 e 54.04 danno lo stesso nome -- ed e' la
    risoluzione con cui queste tensioni si scelgono.

    Il punto decimale diventa un trattino, come li si scrive a mano
    ("Test_SiPM_56-5V_..."): un punto in piu' nel nome di un file e' un
    invito a sbagliare uno split.

    Uscita spenta: "HVoff". Dati presi senza bias non sono dati a 0 V, e
    chiamarli "0-0V" li farebbe sembrare una misura.
    """
    import time
    if not lettura:
        return None
    t = lettura.get("t")
    if t is None or (adesso or time.time()) - t > eta_max:
        return None                      # lettura vecchia: meglio tacere
    if not lettura.get("uscita"):
        return "HVoff"
    try:
        return ("%.1fV" % float(lettura["v_out"])).replace(".", "-")
    except (KeyError, TypeError, ValueError):
        return None


def prefisso(base, et):
    """Il prefisso con l'etichetta data. Idempotente.

    Si toglie sempre quella vecchia prima di mettere la nuova, cosi' la
    funzione si puo' riapplicare a un prefisso gia' etichettato -- per
    esempio dopo un riavvio del controllore a meta' sequenza -- senza
    accumulare pezzi. Con et = None resta il prefisso nudo.
    """
    base = RE_HV.sub("", str(base or "").strip())
    return "%s_%s" % (base, et) if et and base else base
