"""Bordo del self-trigger dei PMT e durata delle run di fondo LNGS, per dare al
MC digitalizzato il trigger di ciascuna run.

    python3 analisi/20261029_normalizzazione_gamma/soglie_lngs.py F1.h5.gz [F2.h5.gz ...]

Perche': la 0011 (offset 200) e la 0013 (offset 100) hanno soglie diverse, e
sommandole il MC va sommato allo stesso modo, ognuno col suo trigger e pesato
con la durata della sua run. La soglia scritta nel log e' in conteggi del
Transparent Mode; quello che serve e' il bordo nell'ampiezza REGISTRATA, e la
calibrazione fra le due si incurva sopra offset ~150: si misura sui dati.

Per ogni file e per ch20/ch21, con le finestre di sempre (piedistallo 0-150,
cancello 240-500 ns), come in 20261030_fondo_0013/libreria_digit.py:
  sigma_q   sigma della carica nel cancello degli eventi vuoti;
  soglia    bordo del trigger: 2 percentile dell'ampiezza degli eventi con un
            solo PMT acceso, che stanno appoggiati alla soglia;
  t50       passaggio al 50% mediano degli stessi eventi (posizione
            dell'impulso: il MC lo prende dalla 0013);
  durata_s  "Acquisition time" dal .log accanto al file.
Scrive measurements/soglie_trigger_lngs.json. Gira dove sono i dati (PC DAQ).
"""
# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
_sys.path.insert(0, _os.path.join(ROOT, "tools"))

import json
import re
import sys
import numpy as np
import padme

DT = 4 / 3


def sig(x):
    x = x[np.abs(x) < 1500]
    m, s_ = x.mean(), x.std()
    for _ in range(5):
        k = np.abs(x - m) < 3.5 * s_
        m, s_ = x[k].mean(), x[k].std()
    return s_


ris = {}
for F in sys.argv[1:]:
    run = _os.path.basename(F).split(".h5")[0]
    log = open(F.split(".h5")[0] + ".log", errors="replace").read()
    durata = float(re.findall(r"Acquisition time:\s*([0-9.]+)", log)[-1])
    offset = re.findall(r"SelfTriggerThresholdOffset \[counts\]: ch20=(\d+) ch21=(\d+)", log)
    h, c, d, s = padme.leggi(F, [20, 21], None, serve_sic=False)
    t = np.arange(d.shape[2]) * DT
    ped = d[:, :, t < 150].mean(2, keepdims=True)
    y = -(d.astype(float) - ped)                     # impulsi dei PMT resi positivi
    g = (t >= 240) & (t < 500)
    q = y[:, :, g].sum(2) * DT
    amp = y[:, :, g].max(2)
    S = np.array([sig(q[:, 0]), sig(q[:, 1])])
    sopra = q > 3 * S[None, :]
    r = {"durata_s": durata, "eventi": int(len(q)), "offset": [int(x) for x in offset[0]] if offset else None,
         "sigma_q": S.tolist(), "soglia": [], "t50": [], "n_un_solo_pmt": []}
    for j in (0, 1):
        solo = np.where(sopra[:, j] & ~sopra[:, 1 - j])[0]
        t50s = []
        for e in solo:
            w = y[e, j]
            p = int(np.argmax(np.where(g, w, -1e9)))
            i = p
            while i > 0 and w[i - 1] >= 0.5 * w[p]:
                i -= 1
            if i > 0:
                t50s.append(t[i - 1] + (0.5 * w[p] - w[i - 1]) / (w[i] - w[i - 1]) * DT)
        r["soglia"].append(float(np.percentile(amp[solo, j], 2)))
        r["t50"].append(float(np.median(t50s)))
        r["n_un_solo_pmt"].append(int(len(solo)))
    ris[run] = r
    print("%s: %.0f s, %d eventi, offset %s; sigma_q %.0f/%.0f ADC*ns; bordo del trigger %.0f/%.0f conteggi "
          "(da %d/%d eventi con un PMT solo); t50 %.1f/%.1f ns"
          % (run, durata, len(q), r["offset"], S[0], S[1], r["soglia"][0], r["soglia"][1],
             r["n_un_solo_pmt"][0], r["n_un_solo_pmt"][1], r["t50"][0], r["t50"][1]))
json.dump(ris, open(_os.path.join(ROOT, "measurements", "soglie_trigger_lngs.json"), "w"), indent=1)
print("scritto measurements/soglie_trigger_lngs.json")
