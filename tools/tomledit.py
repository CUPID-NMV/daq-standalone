#!/usr/bin/env python3
"""Modifica chirurgica di un TOML, una riga per volta.

Esiste per un motivo solo: in questo progetto il file di configurazione e'
documentazione. Ha decine di righe di commento che spiegano perche' una
scelta e' quella che e', quali valori sono ammessi e che cosa e' gia' andato
storto. Rigenerarlo con un dump della libreria lo renderebbe sintatticamente
perfetto e praticamente inservibile.

Quindi qui non si riscrive il file: si trova la riga che definisce la chiave,
dentro la sezione giusta, e si sostituisce il solo valore, lasciando intatti
rientro, allineamento e commento a fine riga.

    chiavi = leggi("config/run-local.toml")
    nuovo, diff = sostituisci(testo, [("digitizer", "NEvents", "3000")])

Solo chiavi gia' presenti: aggiungerne di nuove vorrebbe dire decidere dove
metterle e con quale commento, cioe' iniziare a rovinare proprio la cosa che
questo modulo serve a proteggere.
"""

import re

# Una riga di assegnazione: rientro, nome, spazi, '=', spazi, valore, commento.
# Il valore si prende fino a un '#' che stia fuori dalle virgolette: dentro
# una stringa il cancelletto e' un carattere come un altro.
RIGA = re.compile(r"^(?P<pre>\s*)(?P<chiave>[A-Za-z_][A-Za-z0-9_-]*)"
                  r"(?P<sp1>\s*)=(?P<sp2>\s*)(?P<resto>.*)$")
SEZIONE = re.compile(r"^\s*\[(?P<nome>[^\]]+)\]\s*(?:#.*)?$")


def _taglia_commento(resto):
    """(valore, spazi_prima_del_commento, commento).

    Lo spazio va restituito, non ricalcolato dopo: serve a rimettere il
    commento nella stessa colonna, e dedurlo dal resto della riga da' zero.
    I cancelletti dentro le virgolette non sono commenti.
    """
    dentro = None
    for i, c in enumerate(resto):
        if dentro:
            if c == dentro:
                dentro = None
        elif c in "\"'":
            dentro = c
        elif c == "#":
            testa = resto[:i]
            valore = testa.rstrip()
            return valore, len(testa) - len(valore), resto[i:]
    return resto.rstrip(), 0, ""


def _posizioni(testo):
    """(sezione, chiave) -> indice di riga, per ogni assegnazione non commentata."""
    mappa = {}
    sezione = ""
    for i, riga in enumerate(testo.split("\n")):
        s = SEZIONE.match(riga)
        if s:
            sezione = s.group("nome")
            continue
        if riga.lstrip().startswith("#"):
            continue
        m = RIGA.match(riga)
        if m:
            # La prima vince: se una chiave e' ripetuta il TOML e' gia' invalido,
            # e sara' il parser a dirlo, non questo modulo.
            mappa.setdefault((sezione, m.group("chiave")), i)
    return mappa


def chiavi(testo):
    """Chiavi modificabili presenti nel file, con il loro valore testuale."""
    righe = testo.split("\n")
    fuori = {}
    for (sez, chiave), i in _posizioni(testo).items():
        m = RIGA.match(righe[i])
        valore, _, _ = _taglia_commento(m.group("resto"))
        fuori[(sez, chiave)] = valore
    return fuori


def sostituisci(testo, modifiche):
    """Applica [(sezione, chiave, nuovo_valore), ...].

    Torna (testo_nuovo, elenco_diff). Solleva KeyError sulla prima chiave che
    non esiste: meglio fermarsi che scrivere un file a meta'.
    """
    righe = testo.split("\n")
    mappa = _posizioni(testo)
    diff = []

    for sezione, chiave, nuovo in modifiche:
        if (sezione, chiave) not in mappa:
            raise KeyError("[%s] %s non esiste nel file: aggiungila a mano"
                           % (sezione or "(radice)", chiave))
        i = mappa[(sezione, chiave)]
        m = RIGA.match(righe[i])
        vecchio, spazi, commento = _taglia_commento(m.group("resto"))
        if vecchio == nuovo:
            continue

        # Il commento resta nella stessa colonna finche' il valore nuovo ci
        # sta; se e' piu' lungo lo si spinge avanti di uno spazio soltanto.
        coda = ""
        if commento:
            coda = " " * max(1, len(vecchio) + spazi - len(nuovo)) + commento

        righe[i] = "%s%s%s=%s%s%s" % (m.group("pre"), chiave, m.group("sp1"),
                                      m.group("sp2"), nuovo, coda)
        diff.append({"sezione": sezione, "chiave": chiave,
                     "da": vecchio, "a": nuovo, "riga": i + 1})

    return "\n".join(righe), diff
