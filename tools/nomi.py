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

# "_54-0V" oppure "_HVoff" come pezzo intero del prefisso, fra due "_" o in
# fondo. Serve a toglierla prima di rimetterla: se no a ogni run il prefisso
# cresce di un pezzo. Non solo in fondo: il prefisso resta scritto nel TOML con
# l'etichetta della run prima, e chi ci aggiunge qualcosa a mano la lascia in
# mezzo. Il 2026-11-10 "LNGS__54-0V" + "_PMTOnly_offset-10-10", con l'uscita
# spenta, e' diventato "LNGS__54-0V_PMTOnly_offset-10-10_HVoff": 54 V e HV
# spenta nello stesso nome. Un pezzo di testo dell'utente della forma "_12-5V"
# verrebbe preso per un'etichetta: e' il prezzo, e i nomi non ne hanno.
RE_HV = re.compile(r"_(?:\d+-\dV|HVoff)(?=_|$)")

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
    accumulare pezzi. La nuova va dove stava la vecchia (la prima, se per
    errore ce n'e' piu' d'una), cosi' l'ordine scelto da chi ha scritto il
    prefisso resta; senza una vecchia, in fondo. Con et = None resta il
    prefisso nudo.
    """
    base = str(base or "").strip()
    vecchia = RE_HV.search(base)
    nudo = RE_HV.sub("", base)
    if not et or not nudo:
        return nudo
    if vecchia is None:
        return "%s_%s" % (nudo, et)
    i = vecchia.start()                  # prima della prima etichetta nudo e base coincidono
    return "%s_%s%s" % (nudo[:i], et, nudo[i:])


def prossimo_numero(cartella, prefisso):
    """Il numero del prossimo run col prefisso dato.

    La stessa regola della DAQ (Digitizer::PrepareOutput): i file di DATI
    della cartella che cominciano con "<prefisso>_" e contengono ".h5", le
    quattro cifre subito dopo il prefisso, il massimo piu' uno. Con piu' schede
    il numero lo da' il controllore a tutte (--run): contando i file ognuna lo
    calcolerebbe a seconda di chi ha gia' creato il suo. I file di tutte le
    schede contano, perche' il nome della scheda viene DOPO il numero.
    """
    import os
    massimo = -1
    try:
        nomi_file = os.listdir(cartella)
    except OSError:
        return 0
    testa = prefisso + "_"
    for n in nomi_file:
        if not n.startswith(testa) or ".h5" not in n:
            continue
        cifre = n[len(testa):len(testa) + 4]
        if len(cifre) == 4 and cifre.isdigit():
            massimo = max(massimo, int(cifre))
    return massimo + 1
