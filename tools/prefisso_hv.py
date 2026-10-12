#!/usr/bin/env python3
"""
Mette la tensione dei SiPM nel prefisso dei file, nel TOML.

Serve agli script che avviano la DAQ per conto loro -- lo scan del V812 ne
lancia una per punto -- dove il controllore non e' nel giro e quindi nessuno
aggiorna il nome. Senza questo, quelle run si prenderebbero l'etichetta
rimasta scritta dall'ultima run lanciata dalla pagina: una tensione vecchia,
magari di ieri, dentro un nome che sembra autorevole.

La tensione si chiede al controllore, che e' l'unico a parlare con
l'alimentatore. Se non risponde, l'etichetta si TOGLIE invece di lasciare
quella vecchia: un file senza tensione nel nome e' un fastidio, un file con
la tensione sbagliata e' una misura che fra sei mesi si legge male senza
accorgersene.

    python3 tools/prefisso_hv.py config/run-local.toml
    python3 tools/prefisso_hv.py config/run-local.toml --togli
"""
import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nomi
import tomledit


def lettura_psu(porta, timeout=4.0):
    """L'ultima lettura dell'alimentatore dal controllore, o None."""
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/stato?token=" % porta, timeout=timeout) as r:
            d = json.load(r)
        return ((d.get("psu") or {}).get("lettura")) or None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("toml")
    ap.add_argument("--porta", type=int, default=8766,
                    help="porta del controllore (default 8766)")
    ap.add_argument("--togli", action="store_true",
                    help="toglie l'etichetta senza chiedere niente a nessuno")
    a = ap.parse_args()

    try:
        testo = open(a.toml, encoding="utf-8").read()
    except OSError as e:
        sys.exit("non riesco a leggere %s: %s" % (a.toml, e))

    chiavi = tomledit.chiavi(testo)
    base = None
    for (sez, chiave), valore in chiavi.items():
        if (sez, chiave) == ("digitizer", "OutputFile"):
            base = str(valore).strip().strip('"').strip("'")
    if base is None:
        sys.exit("in %s non c'e' [digitizer] OutputFile." % a.toml)

    et = None if a.togli else nomi.etichetta(lettura_psu(a.porta))
    nuovo = nomi.prefisso(base, et)

    if nuovo == base:
        print("prefisso gia' giusto: %s" % base)
        return
    testo2, _ = tomledit.sostituisci(testo, [("digitizer", "OutputFile", '"%s"' % nuovo)])
    with open(a.toml, "w", encoding="utf-8") as f:
        f.write(testo2)
    if et:
        print("prefisso: %s -> %s" % (base, nuovo))
    else:
        print("prefisso: %s -> %s   (tensione non disponibile: etichetta tolta, "
              "meglio senza che sbagliata)" % (base, nuovo))


if __name__ == "__main__":
    main()
