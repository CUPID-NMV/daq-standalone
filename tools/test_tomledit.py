#!/usr/bin/env python3
"""Prove di tomledit. Non serve hardware: si lancia ovunque.

    python3 tools/test_tomledit.py

Questo modulo riscrive il file di configurazione dell'utente, che in questo
progetto e' anche documentazione: un errore qui non rompe una run, cancella
spiegazioni che non si recuperano. Da cui una prova vera invece di una
prova a occhio.
"""

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tomledit

try:
    import tomllib
except ImportError:
    tomllib = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CAMPIONE = '''# intestazione
[digitizer]
NEvents         = 1500000
Threshold   = [14, 14]   # mV, in modulo: il segnale e' negativo
OutputFile      = "PMT#selftrig"      # il cancelletto qui e' dentro le virgolette
# SelfTrigger  = true    <-- commentata, non si deve toccare
SelfTrigger     = false

[cfd]
Threshold   = [7, 7]
NEvents = 1
'''

ok = falliti = 0


def prova(nome, cond, extra=""):
    global ok, falliti
    if cond:
        ok += 1
        print("  OK       %s" % nome)
    else:
        falliti += 1
        print("  FALLITA  %s   %s" % (nome, extra))


def riga_con(testo, inizio):
    return [r for r in testo.split("\n") if r.startswith(inizio)][0]


def main():
    n, d = tomledit.sostituisci(CAMPIONE, [("digitizer", "NEvents", "3000")])
    prova("cambia il valore", "NEvents         = 3000" in n)
    prova("il diff riporta il valore precedente", len(d) == 1 and d[0]["da"] == "1500000")

    n, _ = tomledit.sostituisci(CAMPIONE, [("digitizer", "Threshold", "[20, 20]")])
    r = riga_con(n, "Threshold")
    prova("conserva il commento", "# mV, in modulo" in r, r)
    prova("il commento resta nella stessa colonna",
          r.index("#") == riga_con(CAMPIONE, "Threshold").index("#"), r)

    n, _ = tomledit.sostituisci(CAMPIONE, [("digitizer", "Threshold", "[2000, 2000]")])
    r = riga_con(n, "Threshold")
    prova("valore piu' lungo: commento spinto avanti, non mangiato",
          "[2000, 2000] #" in r, r)

    n, _ = tomledit.sostituisci(CAMPIONE, [("digitizer", "OutputFile", '"nuovo"')])
    r = riga_con(n, "OutputFile")
    prova("il cancelletto dentro le virgolette non e' un commento",
          'OutputFile      = "nuovo"' in r and "dentro le virgolette" in r, r)

    n, _ = tomledit.sostituisci(CAMPIONE, [("cfd", "Threshold", "[9, 9]")])
    prova("tocca la sezione giusta",
          "Threshold   = [14, 14]" in n and "Threshold   = [9, 9]" in n)

    n, _ = tomledit.sostituisci(CAMPIONE, [("cfd", "NEvents", "5")])
    prova("stesso nome in due sezioni",
          "NEvents         = 1500000" in n and "NEvents = 5" in n)

    k = tomledit.chiavi(CAMPIONE)
    prova("una riga commentata non e' una chiave",
          k.get(("digitizer", "SelfTrigger")) == "false")

    try:
        tomledit.sostituisci(CAMPIONE, [("digitizer", "NonEsiste", "1")])
        prova("chiave assente solleva KeyError", False)
    except KeyError as e:
        prova("chiave assente solleva KeyError", "aggiungila a mano" in str(e))

    n, d = tomledit.sostituisci(CAMPIONE, [("digitizer", "NEvents", "1500000")])
    prova("valore identico: file intatto", n == CAMPIONE and d == [])

    # --- sul file versionato vero ---
    percorso = os.path.join(ROOT, "config", "template-daq.toml")
    if not (tomllib and os.path.exists(percorso)):
        print("\n(salto le prove sul file vero: manca tomllib o il template)")
    else:
        testo = io.open(percorso, encoding="utf-8").read()
        prima = tomllib.loads(testo)
        n, _ = tomledit.sostituisci(testo, [("digitizer", "NEvents", "4242"),
                                            ("digitizer", "SamplingRate", '"2.5GHz"'),
                                            ("settings", "verbosity", "4")])
        dopo = tomllib.loads(n)
        prova("il template resta TOML valido", True)
        prova("i tre valori sono cambiati",
              dopo["digitizer"]["NEvents"] == 4242 and
              dopo["digitizer"]["SamplingRate"] == "2.5GHz" and
              dopo["settings"]["verbosity"] == 4)
        diversi = sorted(k for k in prima["digitizer"]
                         if prima["digitizer"][k] != dopo["digitizer"].get(k))
        prova("nient'altro e' cambiato", diversi == ["NEvents", "SamplingRate"], diversi)
        cambiate = [i for i, (a, b) in enumerate(zip(testo.split("\n"), n.split("\n")))
                    if a != b]
        prova("tre righe toccate, non una di piu'", len(cambiate) == 3, cambiate)
        prova("nessun commento perso",
              testo.count("#") == n.count("#"),
              "%d prima, %d dopo" % (testo.count("#"), n.count("#")))

    print("\n%d passate, %d fallite" % (ok, falliti))
    return 1 if falliti else 0


if __name__ == "__main__":
    sys.exit(main())
