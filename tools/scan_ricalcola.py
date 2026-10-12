#!/usr/bin/env python3
"""
Ricalcola le durate di uno scan del V812 dal tempo dichiarato dalla DAQ.

    python3 tools/scan_ricalcola.py plots/scan_v812_*.json
    python3 tools/scan_ricalcola.py --prova plots/scan_v812_2026*.json

Serve a riparare gli scan presi prima del 2026-10-13. Fino a quel giorno la
durata di ogni punto era "istante dopo la morte del processo meno StartTime",
e dentro ci finivano la chiusura e la COMPRESSIONE del file, che dura quanto
il file e' grosso. Il punto a soglia piu' bassa ha il rate piu' alto, quindi
il file piu' grande e la compressione piu' lunga: usciva a 449 Hz invece di
533, e la curva aveva una gobba iniziale -- il primo punto sembrava avere meno
rate del secondo, che per uno scan in soglia e' impossibile.

Il tempo giusto lo stampa la DAQ nel log di ogni run ("Acquisition time"), e
finche' quei .log sono sul disco la misura si recupera senza rifarla.

L'originale viene salvato accanto con estensione .bak, una volta sola.
"""
import argparse
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def tempo_daq(data_dir, nome_file):
    """I secondi di acquisizione dichiarati dalla DAQ, o None."""
    base = nome_file.replace(".h5.gz", "").replace(".h5", "")
    log = os.path.join(data_dir, base + ".log")
    try:
        m = re.search(r"Acquisition time:\s*([0-9.]+)",
                      open(log, errors="replace").read())
    except OSError:
        return None
    return float(m.group(1)) if m else None


def ripara(path, data_dir, prova=False):
    d = json.load(open(path))
    if d.get("tipo") != "v812":
        return None                      # gli scan del self-trigger non hanno il difetto
    cambiati, senza_log = [], 0
    for p in d.get("punti", []):
        t = tempo_daq(data_dir, p.get("file", ""))
        if not t:
            senza_log += 1
            continue
        vecchio = p.get("rate", 0.0)
        p["durata_s"] = round(t, 2)
        p["rate"] = round((p.get("eventi") or 0) / t, 4)
        if abs(p["rate"] - vecchio) > 0.5:
            cambiati.append((p["soglia_mv"], vecchio, p["rate"]))
    if not prova and cambiati:
        if not os.path.exists(path + ".bak"):
            shutil.copy(path, path + ".bak")
        d["nota_ricalcolo"] = ("durate riprese dal tempo di acquisizione "
                               "dichiarato dalla DAQ")
        json.dump(d, open(path, "w"), indent=2)
    return cambiati, senza_log


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json", nargs="+")
    ap.add_argument("-d", "--data-dir", default=os.path.join(ROOT, "data"))
    ap.add_argument("--prova", action="store_true",
                    help="dice cosa cambierebbe senza scrivere niente")
    a = ap.parse_args()

    for path in a.json:
        esito = ripara(path, a.data_dir, a.prova)
        if esito is None:
            print("%-44s non e' uno scan del V812, lasciato com'e'"
                  % os.path.basename(path))
            continue
        cambiati, senza_log = esito
        nome = os.path.basename(path)
        if not cambiati:
            print("%-44s gia' a posto%s" % (nome,
                  "" if not senza_log else "  (%d punti senza log)" % senza_log))
            continue
        peggio = max(cambiati, key=lambda c: abs(c[2] - c[1]) / max(c[1], 1e-9))
        print("%-44s %d punti corretti, il peggiore a %.0f mV: %.1f -> %.1f Hz (%+.0f%%)%s"
              % (nome, len(cambiati), peggio[0], peggio[1], peggio[2],
                 100 * (peggio[2] - peggio[1]) / max(peggio[1], 1e-9),
                 "" if not senza_log else "  [%d punti senza log]" % senza_log))
    if a.prova:
        print("\n(prova: non ho scritto niente)", file=sys.stderr)


if __name__ == "__main__":
    main()
