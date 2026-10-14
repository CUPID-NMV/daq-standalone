#!/usr/bin/env python3
"""
Il rapporto fra le due catene, misurato sulle forme d'onda di UNO scan del CFD.

    python3 tools/rapporto_dalle_forme.py plots/scan_v812_20261013_092419.json

Sovrapporre due scan presi in momenti diversi non decide: la normalizzazione
libera assorbe la differenza, e due scan del CFD della stessa notte possono
differire fra loro di un fattore 1.6 nella coda perche' nel frattempo e'
cambiato il guadagno. Qui non si sovrappone niente.

L'idea: gli eventi di uno scan del CFD **li registra il digitizer**. A una
soglia V del CFD, ogni evento registrato ha avuto ampiezza maggiore di V
all'ingresso del CFD, cioe' maggiore di V/g all'ingresso del digitizer. Il
BORDO BASSO della distribuzione delle ampiezze misurate nelle tracce vale
quindi V/g, in millivolt assoluti.

Un punto dello scan, un numero. La retta su tutti i punti da' g, senza
normalizzazioni, senza ipotesi sul tempo, e senza mai confrontare due
acquisizioni diverse fra loro: ogni punto e' autoconsistente, perche' la
soglia e le ampiezze vengono dalla STESSA run.

Lo stesso conto, fatto su scan presi in momenti diversi, dice anche se g e'
cambiato -- cioe' se qualcuno ha toccato i cavi.
"""
import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daqio                                             # noqa: E402
from calibra_selftrigger import ampiezze_mV, marca_spuri  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def trova(data_dir, nome):
    if not nome:
        return None
    base = os.path.join(data_dir, os.path.basename(nome))
    for c in (base, base + ".gz", base.replace(".h5", "") + ".h5.gz"):
        if os.path.exists(c):
            return c
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scan", help="il JSON di uno scan del V812")
    ap.add_argument("-d", "--data-dir", default=os.path.join(ROOT, "data"))
    ap.add_argument("-c", "--canali", default="16,17,18",
                    help="canali del DIGITIZER su cui stanno i rivelatori")
    ap.add_argument("-n", "--max-eventi", type=int, default=4000,
                    help="quanti eventi leggere per punto (il bordo basso ne vuole pochi)")
    ap.add_argument("--minimo-eventi", type=int, default=300)
    ap.add_argument("-o", "--out", default=None)
    a = ap.parse_args()

    d = json.load(open(a.scan))
    canali = [int(c) for c in a.canali.split(",")]
    print("scan   : %s  (%s)" % (os.path.basename(a.scan), d.get("quando")))
    print("canali : %s del digitizer" % ", ".join(str(c) for c in canali))
    print()
    print("%8s %10s %12s %12s   %s" % ("V [mV]", "eventi", "minimo mV", "1% mV", "run"))

    righe = []
    t0 = time.time()
    for p in d["punti"]:
        h5 = trova(a.data_dir, p.get("file"))
        if not h5:
            continue
        try:
            _, dati = daqio.load(h5, max_events=a.max_eventi, channels=canali)
        except Exception as e:                            # file troncato, canali assenti
            print("%8.0f  %s" % (p["soglia_mv"], str(e)[:60]))
            continue
        if len(dati) < a.minimo_eventi:
            continue
        amp, per_canale = ampiezze_mV(dati)
        amp = amp[~marca_spuri(per_canale)]
        if len(amp) < a.minimo_eventi:
            continue
        q1 = float(np.quantile(amp, 0.01))
        righe.append((float(p["soglia_mv"]), len(amp), float(amp.min()), q1))
        print("%8.0f %10d %12.2f %12.2f   %s"
              % (p["soglia_mv"], len(amp), amp.min(), q1, os.path.basename(h5)))

    print("\nletti in %.0f s" % (time.time() - t0))
    if len(righe) < 3:
        sys.exit("troppi pochi punti")

    # V = g * bordo: la pendenza E' g. Si fitta in questo verso e non
    # nell'altro perche' l'incertezza sta nel bordo misurato, non nella soglia
    # scritta nel registro del V812, che e' esatta per costruzione.
    x = np.array([r[3] for r in righe])      # bordo basso, al digitizer
    y = np.array([r[0] for r in righe])      # soglia, al CFD
    g, inter = np.polyfit(x, y, 1)
    res = y - (g * x + inter)
    s = float(np.sqrt(np.sum(res ** 2) / (len(x) - 2)))
    err = s / np.sqrt(np.sum((x - x.mean()) ** 2))

    print()
    print("g = %.3f +- %.3f   (ampiezza al CFD / ampiezza al digitizer)" % (g, err))
    print("  intercetta : %+.2f mV al CFD" % inter)
    print("  residui    : %.2f mV" % s)
    print("  punti      : %d" % len(x))

    if a.out:
        json.dump({"scan": os.path.basename(a.scan), "quando": d.get("quando"),
                   "canali_digitizer": canali, "g": g, "errore_g": float(err),
                   "intercetta_mv": inter, "residui_mv": s,
                   "punti": [{"soglia_cfd_mv": r[0], "eventi": r[1],
                              "minimo_mv": r[2], "q1_mv": r[3]} for r in righe]},
                  open(a.out, "w"), indent=2)
        print("\ndati grezzi in %s" % a.out)


if __name__ == "__main__":
    main()
