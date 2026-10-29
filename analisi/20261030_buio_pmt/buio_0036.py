"""Impulsi di buio dei PMT nella run a trigger software 0036: rate e spettro
del fotoelettrone singolo, cioe' la scala ADC*ns per PE misurata.

    python3 analisi/20261030_buio_pmt/buio_0036.py F_0036 OUT.png

Le finestre sono a istanti casuali (trigger software), quindi gli impulsi che
contengono sono di buio, non legati al trigger. Si cercano fuori da:
  - i primi 80 ns e gli ultimi 30 campioni (bordi della traccia);
  - 550-800 ns, dove cadono le raffiche a ~176 MHz agganciate al trigger
    software (CLAUDE.md, 2026-10-26: 661-704 ns a PostTrigger 50%).
Impulso: massimo locale del segnale (piedistallo - ADC, impulsi negativi)
sopra SOGLIA conteggi, almeno 30 ns dal precedente. Carica: somma da 10 ns
prima a 40 ns dopo il picco, col piedistallo preso da 40 a 15 ns prima.
Il tempo vivo e' il numero di finestre per il tratto utile.

Confronto: la scala K dei muoni (measurements/mc_digit_K.json) con la forma
dell'impulso della 0013 prevede l'ampiezza di un PE.
"""
# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
_sys.path.insert(0, _os.path.join(ROOT, "tools"))

import json
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import padme

F, OUT = sys.argv[1], sys.argv[2]
SOGLIA = 12.0
dt = 4 / 3
h, c, d, s = padme.leggi(F, [20, 21], None)
n_ev, _, ns = d.shape
t = np.arange(ns) * dt
utile = (t > 80) & (t < t[-30]) & ~((t > 550) & (t < 800))
T_VIVO = n_ev * utile.sum() * dt * 1e-9                    # s per canale
MIS = _os.path.join(ROOT, "measurements")
K = json.load(open(_os.path.join(MIS, "mc_digit_K.json")))["K_adcns_per_pe"]
lib = np.load(_os.path.join(MIS, "libreria_digit_0013.npz"))
A1_ATTESA = {20: K * lib["forma_ch20"].max(), 21: K * lib["forma_ch21"].max()}
i_pre0, i_pre1 = int(40 / dt), int(15 / dt)
i_a, i_b = int(10 / dt), int(40 / dt)
gap = int(30 / dt)

ris = {}
for j, ch in enumerate((20, 21)):
    y = -(d[:, j, :].astype(np.float32) - np.median(d[:, j, :], axis=1, keepdims=True))
    rms = float(np.median(np.std(y[:, utile], axis=1)))
    amp, q, esempi = [], [], []
    cand = (y > SOGLIA) & utile[None, :]
    cand[:, 1:-1] &= (y[:, 1:-1] >= y[:, :-2]) & (y[:, 1:-1] >= y[:, 2:])
    for e, k in zip(*np.nonzero(cand)):
        if k < i_pre0 or k + i_b >= ns:
            continue
        if amp and esempi and esempi[-1][0] == e and k - esempi[-1][1] < gap:
            continue
        base = y[e, k - i_pre0:k - i_pre1].mean()
        amp.append(y[e, k] - base)
        q.append((y[e, k - i_a:k + i_b] - base).sum() * dt)
        esempi.append((e, k))
    amp, q = np.array(amp), np.array(q)
    ris[ch] = dict(amp=amp, q=q, rms=rms, esempi=esempi, y=y)
    print("ch%d: rumore %.2f conteggi rms; %d impulsi > %.0f conteggi in %.4f s -> %.0f Hz; "
          "ampiezza mediana %.0f, carica mediana %.0f ADC*ns; attesa per 1 PE dai muoni: ampiezza %.0f, carica %.0f"
          % (ch, rms, len(amp), SOGLIA, T_VIVO, len(amp) / T_VIVO, np.median(amp) if len(amp) else 0,
             np.median(q) if len(q) else 0, A1_ATTESA[ch], K))

fig = plt.figure(figsize=(19, 13))
gs = fig.add_gridspec(3, 4)
for j, ch in enumerate((20, 21)):
    r = ris[ch]
    a = fig.add_subplot(gs[0, 2 * j])
    a.hist(r["amp"], np.linspace(0, 400, 101), histtype="step", color="k", lw=1.4,
           label="dark pulses: %d in %.3f s = %.0f Hz (> %.0f counts)" % (len(r["amp"]), T_VIVO, len(r["amp"]) / T_VIVO, SOGLIA))
    a.axvline(A1_ATTESA[ch], color="#e8710a", ls="--", lw=1.4,
              label="1 PE expected from the muon scale: %.0f counts" % A1_ATTESA[ch])
    a.axvline(lib["sogliaA_ch%d" % ch], color="#2a6fdb", ls=":", lw=1.4,
              label="trigger threshold at offset 100: %.0f counts" % lib["sogliaA_ch%d" % ch])
    a.set_yscale("log")
    a.set_xlabel("ch%d pulse amplitude [counts]  (noise %.1f counts rms)" % (ch, r["rms"]))
    a.set_ylabel("pulses / 4 counts")
    a.set_title("ch%d — amplitude of the dark pulses (run 0036, software trigger)" % ch, fontsize=10, loc="left")
    a.legend(fontsize=7.8, frameon=False)
    a.grid(alpha=0.25, which="both")
    a = fig.add_subplot(gs[0, 2 * j + 1])
    a.hist(r["q"], np.linspace(-500, 6000, 131), histtype="step", color="k", lw=1.4, label="dark pulses")
    a.axvline(K, color="#e8710a", ls="--", lw=1.4, label="1 PE from the muon scale: K = %.0f ADC·ns" % K)
    a.set_yscale("log")
    a.set_xlabel("ch%d pulse charge [ADC·ns]  (−10/+40 ns around the peak)" % ch)
    a.set_ylabel("pulses / 50 ADC·ns")
    a.set_title("ch%d — charge of the dark pulses" % ch, fontsize=10, loc="left")
    a.legend(fontsize=7.8, frameon=False)
    a.grid(alpha=0.25, which="both")
    for k_, (e, k) in enumerate(r["esempi"][:4]):
        a = fig.add_subplot(gs[1 + j, k_])
        a.plot(t, -r["y"][e], color="#e8710a" if ch == 20 else "#c23b5a", lw=0.7)
        a.axvspan(550, 800, color="#eee", zorder=0)
        a.set_xlim(max(0, t[k] - 150), min(t[-1], t[k] + 250))
        a.set_title("ch%d, window %d: pulse at %.0f ns, A = −%.0f, Q = %.0f" % (ch, e, t[k], r["amp"][k_], r["q"][k_]),
                    fontsize=8.5, loc="left")
        a.set_xlabel("time [ns]")
        a.set_ylabel("ADC − baseline [counts]")
        a.grid(alpha=0.25)
fig.suptitle("PMT dark pulses in the software-trigger run 0036 (random windows, 750 MS/s): rate and "
             "single-photoelectron scale — grey band = excluded trigger bursts", fontsize=11)
fig.tight_layout()
fig.savefig(OUT, dpi=90)
np.savez_compressed(_os.path.join(MIS, "buio_0036.npz"), amp20=ris[20]["amp"], q20=ris[20]["q"],
                    amp21=ris[21]["amp"], q21=ris[21]["q"], t_vivo_s=T_VIVO, soglia=SOGLIA)
