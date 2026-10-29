"""Dalla run LNGS 0013: libreria di tracce di solo piedistallo e forma
dell'impulso, per digitalizzare il MC come i dati veri.

    python3 analisi/20261030_fondo_0013/libreria_digit.py F_0013 [N_TRACCE]

Scrive measurements/libreria_digit_0013.npz con, per ch20 e ch21:
  rumore_chXX   tracce GREZZE (int16, 1012 campioni, correzione CAEN come nei
                dati) di eventi in cui quel PMT non ha impulso: |carica| <
                3 sigma e nessun campione oltre 15 conteggi dal piedistallo.
                Sono il rumore vero: ondulazione della base, oscillazione dei
                primi 100 ns, struttura del DRS4. L'impulso MC si somma sopra.
  forma_chXX    forma media dell'impulso degli eventi con UN solo PMT acceso
                (lampi veloci, FWHM ~8 ns), normalizzata ad AREA 1 (1/ns) e
                campionata a passo fine (FINE_NS) attorno al passaggio al 50%.
                Fa da risposta del singolo fotoelettrone: contiene PMT,
                scheda di amplificazione, coda e sottoelongazione. E' una
                ipotesi -- e' la risposta a un lampo di qualche PE, non a un
                PE singolo -- finche' il PE singolo non e' misurato.
  t50_chXX      tempo medio del passaggio al 50% negli eventi veri (ns).
  sogliaA_chXX  ampiezza (conteggi) del bordo del trigger: il 2 percentile
                dell'ampiezza degli eventi con un solo PMT, che stanno
                appoggiati alla soglia.
"""
# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
_sys.path.insert(0, _os.path.join(ROOT, "tools"))

import sys
import numpy as np
import padme

F = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 1500
FINE_NS = 0.1
h, c, d, s = padme.leggi(F, [20, 21], None)
dt = 4 / 3
t = np.arange(d.shape[2]) * dt
ped = d[:, :, t < 150].mean(2, keepdims=True)
y = -(d.astype(float) - ped)                         # impulsi resi positivi
g = (t >= 240) & (t < 500)
q = y[:, :, g].sum(2) * dt
amp = y[:, :, g].max(2)


def sig(x):
    x = x[np.abs(x) < 1500]
    m, s_ = x.mean(), x.std()
    for _ in range(5):
        k = np.abs(x - m) < 3.5 * s_
        m, s_ = x[k].mean(), x[k].std()
    return s_


S = np.array([sig(q[:, 0]), sig(q[:, 1])])
sopra = q > 3 * S[None, :]
out = {"dt_ns": dt, "fine_ns": FINE_NS, "sigma_q": S}
rng = np.random.default_rng(1)
xf = np.arange(-20, 300, FINE_NS)
for j, ch in enumerate((20, 21)):
    vuoti = np.where((np.abs(q[:, j]) < 3 * S[j]) & (np.abs(y[:, j]).max(1) < 15))[0]
    scelti = rng.choice(vuoti, min(N, len(vuoti)), replace=False)
    out["rumore_ch%d" % ch] = d[scelti, j, :].astype(np.int16)
    solo = np.where(sopra[:, j] & ~sopra[:, 1 - j])[0]
    forme, t50s = [], []
    for e in solo:
        w = y[e, j]
        p = int(np.argmax(np.where(g, w, -1e9)))
        so = 0.5 * w[p]
        i = p
        while i > 0 and w[i - 1] >= so:
            i -= 1
        if i == 0:
            continue
        t0 = t[i - 1] + (so - w[i - 1]) / (w[i] - w[i - 1]) * dt
        t50s.append(t0)
        forme.append(np.interp(xf + t0, t, w, left=0.0, right=0.0))
    f = np.mean(forme, axis=0)
    f /= f.sum() * FINE_NS                           # area 1, in 1/ns
    out["forma_ch%d" % ch] = f
    out["forma_x_ns"] = xf
    out["t50_ch%d" % ch] = float(np.median(t50s))
    out["sogliaA_ch%d" % ch] = float(np.percentile(amp[solo, j], 2))
    print("ch%d: %d tracce di rumore (su %d vuote), forma da %d impulsi, t50 %.1f ns, bordo trigger %.0f conteggi"
          % (ch, len(scelti), len(vuoti), len(forme), out["t50_ch%d" % ch], out["sogliaA_ch%d" % ch]))
np.savez_compressed(_os.path.join(ROOT, "measurements", "libreria_digit_0013.npz"), **out)
print("scritto measurements/libreria_digit_0013.npz")
