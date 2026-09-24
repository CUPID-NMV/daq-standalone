#!/usr/bin/env python3
"""Calibrazione offset->mV in funzione della larghezza dell'impulso."""
import json, os, sys
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

CONTEGGIO_MV = 1000.0 / 4096      # 1 Vpp su 12 bit = 0.244 mV per conteggio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    ROOT, "measurements", "larghezza_impulso_20260924.json")
m = json.load(open(src))
p = sorted(m["punti"], key=lambda x: x["larghezza_ns"])

w = np.array([x["larghezza_ns"] for x in p])
v = np.array([x["mv_per_offset"] for x in p])
# incertezza propagata dal taglio
e = np.array([x["ampiezza_mv"] * x["err_conteggi"] / x["taglio_conteggi"]**2 for x in p])
lim = np.array([bool(x.get("limite_inferiore")) for x in p])

fig, ax = plt.subplots(figsize=(7.6, 5.0))
ax.errorbar(w[~lim], v[~lim], yerr=e[~lim], fmt="o", ms=8, capsize=4, lw=1.6,
            color="#1f77b4", label="misura")
# i limiti inferiori si disegnano con la freccia verso l'alto, non con la barra
if lim.any():
    ax.errorbar(w[lim], v[lim], yerr=[np.zeros(lim.sum()), 0.45 * v[lim]],
                fmt="o", ms=8, lw=1.6, color="#c0392b", uplims=False, lolims=True,
                label="limite inferiore")
for x, y, l in zip(w, v, lim):
    ax.annotate(f"{y:.3f}" if not l else f"$\\geq${y:.2f}", xy=(x, y),
                xytext=(8, -4), textcoords="offset points", fontsize=9,
                color="#c0392b" if l else "#1f77b4")

ax.axhspan(0.45, 0.49, color="#1f77b4", alpha=.10)
ax.text(30, 0.52, "regime a banda larga:\nnessuna dipendenza", fontsize=9, color="#444")

# la larghezza degli impulsi dei PMT, ora misurata e non piu' estrapolata
ax.axvspan(1.4, 2.2, color="#2ca02c", alpha=.12)
ax.annotate("impulsi PMT\n1.6-2.0 ns", xy=(1.8, 0.62), ha="center",
            fontsize=9, color="#1a6b1a")

ax.set_xscale("log"); ax.set_yscale("log")
ax.set_xlabel("larghezza dell'impulso a meta' altezza  [ns]")
ax.set_ylabel("mV per unita' di offset")
ax.set_title("Calibrazione della soglia contro larghezza dell'impulso", fontsize=12)
ax.set_xlim(1.0, 160)
ax.set_ylim(0.35, 12)
ax.grid(alpha=.3, which="both")
ax.legend(fontsize=9, loc="upper right")

# secondo asse: quanto segnale perde il comparatore rispetto all'Output Mode
ax2 = ax.twinx()
ax2.set_yscale("log")
ax2.set_ylim(0.35 / CONTEGGIO_MV, 12 / CONTEGGIO_MV)
ax2.set_ylabel("attenuazione vista dal comparatore  [x]")

out = os.path.join(ROOT, "plots", "larghezza_impulso.png")
os.makedirs(os.path.dirname(out), exist_ok=True)
fig.tight_layout(); fig.savefig(out, dpi=130)
print("grafico:", out)
