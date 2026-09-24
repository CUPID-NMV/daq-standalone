#!/usr/bin/env python3
"""Calibrazione offset->mV in funzione della larghezza dell'impulso."""
import json, os, sys
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    ROOT, "measurements", "larghezza_impulso_20260924.json")
m = json.load(open(src))
p = m["punti"]

w = np.array([x["larghezza_ns"] for x in p])
v = np.array([x["mv_per_offset"] for x in p])
# incertezza propagata dal taglio
e = np.array([x["ampiezza_mv"] * x["err_conteggi"] / x["taglio_conteggi"]**2 for x in p])

fig, ax = plt.subplots(figsize=(7.2, 4.6))
ax.errorbar(w, v, yerr=e, fmt="o", ms=8, capsize=4, lw=1.6, color="#1f77b4")
for x, y, ee in zip(w, v, e):
    ax.annotate(f"{y:.3f}", xy=(x, y), xytext=(6, 8),
                textcoords="offset points", fontsize=9)

ax.axhspan(0.45, 0.49, color="#1f77b4", alpha=.10)
ax.text(60, 0.50, "regime a banda larga:\nnessuna dipendenza", fontsize=9, color="#444")

# zona dei PMT, non misurata
ax.axvspan(1.2, 3.0, color="#c0392b", alpha=.12)
ax.annotate("impulsi PMT\n~2 ns\n(non misurato)", xy=(2.0, 0.95), ha="center",
            fontsize=9, color="#a03020")

ax.set_xscale("log")
ax.set_xlabel("larghezza dell'impulso a metà altezza  [ns]")
ax.set_ylabel("mV per unità di offset")
ax.set_title("Calibrazione della soglia contro larghezza dell'impulso", fontsize=12)
ax.set_ylim(0.3, 1.1)
ax.grid(alpha=.3, which="both")

out = os.path.join(ROOT, "plots", "larghezza_impulso.png")
os.makedirs(os.path.dirname(out), exist_ok=True)
fig.tight_layout(); fig.savefig(out, dpi=130)
print("grafico:", out)
