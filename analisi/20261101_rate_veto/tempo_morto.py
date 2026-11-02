"""Tempo morto di CUPID dovuto al veto: ogni trigger del WC apre una finestra di durata TAU
in cui gli eventi dei bolometri si scartano.

    python3 analisi/20261101_rate_veto/tempo_morto.py [--tau-ms 1] [--out PREFISSO]

Con i trigger del veto poissoniani a rate R, la frazione di tempo coperta dalle finestre e'
1 - exp(-R*TAU) se le finestre che si sovrappongono si fondono, come quando si scartano a
posteriori gli eventi dei bolometri entro TAU da un trigger; e' R*TAU/(1 + R*TAU) se la
finestra e' un gate hardware che non si riarma mentre e' aperto. R*TAU da solo conta due volte
le sovrapposizioni. Una finestra di +-TAU attorno al trigger conta come 2*TAU.

I rate del veto completo vengono da rate_veto.py e rate_soglia_carica.py (prototipo al LNGS,
run 0013, scalato all'area d'acqua del veto): almeno un PMT sopra 1.8 PE, con gli impulsi
propri dei PMT scalati con l'area o col numero di PMT, e -- per confronto -- entrambi i PMT
dello stesso modulo sopra soglia, che toglie quasi tutti gli impulsi propri (sono eventi di un
PMT solo).
"""
# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))

import argparse
import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ap = argparse.ArgumentParser()
ap.add_argument("--tau-ms", type=float, default=1.0)
ap.add_argument("--out", default=_os.path.join(ROOT, "measurements", "tempo_morto_veto_20261102"))
arg = ap.parse_args()
MIS = _os.path.join(ROOT, "measurements")
TAU = arg.tau_ms * 1e-3

V = json.load(open(_os.path.join(MIS, "rate_veto_20261101.json")))
S = json.load(open(_os.path.join(MIS, "rate_veto_20261101_soglia.json")))
RAP = V["rapporto_aree"]
r13 = V["rate"]["0013"]


def fuse(R):
    return 1 - np.exp(-R * TAU)


def gate(R):
    return R * TAU / (1 + R * TAU)


scenari = [
    ("at least one PMT ≥ 1.8 PE, everything scaled with the water area", r13["veto_tutto_area_hz"], "#d62728"),
    ("same, the PMTs' own pulses scaled with the number of PMTs", r13["veto_separato_hz"], "#e8710a"),
    ("both PMTs of a module ≥ 1.8 PE (charge), scaled with the area",
     S["rate_sopra_soglia"]["1.8"]["entrambi"]["hz"] * RAP, "#1f4e9c"),
    ("both PMTs of a module ≥ 3 PE (charge), scaled with the area",
     S["rate_sopra_soglia"]["3.0"]["entrambi"]["hz"] * RAP, "#6a8fd0"),
]
ris = []
print("finestra di veto %.3g ms" % arg.tau_ms)
for nome, R, _ in scenari:
    ris.append(dict(scenario=nome, rate_hz=R, morto_finestre_fuse=fuse(R), morto_gate=gate(R),
                    morto_finestra_doppia=1 - np.exp(-2 * R * TAU), R_per_tau=R * TAU))
    print("  %-75s %6.1f Hz -> %5.1f%% (gate %5.1f%%; finestra +-tau %5.1f%%)"
          % (nome, R, 100 * fuse(R), 100 * gate(R), 100 * (1 - np.exp(-2 * R * TAU))))
soglie = {p: -np.log(1 - p / 100) / TAU for p in (1, 5)}
print("  rate per un tempo morto dell'1%%: %.1f Hz; del 5%%: %.1f Hz" % (soglie[1], soglie[5]))
json.dump(dict(tau_s=TAU, scenari=ris, rate_per_1_percento_hz=soglie[1], rate_per_5_percento_hz=soglie[5]),
          open(arg.out + ".json", "w"), indent=1)

fig, a = plt.subplots(figsize=(11, 6.2))
R = np.logspace(0, np.log10(3000), 400)
a.plot(R, 100 * fuse(R), color="k", lw=2.2, label="windows merge when they overlap (offline veto of the bolometer events): "
       "1 − exp(−R·τ)")
a.plot(R, 100 * gate(R), color="k", lw=1.3, ls="--", label="non-retriggerable hardware gate: R·τ / (1 + R·τ)")
for nome, Rv, col in scenari:
    a.plot([Rv], [100 * fuse(Rv)], "o", ms=10, color=col, mec="white", mew=1.5, zorder=5,
           label="%s: %.0f Hz → %.1f%%" % (nome, Rv, 100 * fuse(Rv)))
for p, Rp in soglie.items():
    a.axhline(p, color="#999", lw=0.8, ls=":")
    a.text(1.15, p * 1.08, "%d%% dead time: R = %.0f Hz" % (p, Rp), fontsize=9, color="#666")
a.set_xscale("log")
a.set_yscale("log")
a.set_xlim(1, 3000)
a.set_ylim(0.08, 100)
a.set_xlabel("veto trigger rate on the full WC, R [Hz]")
a.set_ylabel("CUPID dead time [%]")
a.set_title("dead time from a %.3g ms veto window after every WC trigger (Poisson triggers); rates extrapolated from the "
            "LNGS prototype, run 0013" % arg.tau_ms, fontsize=9.5, loc="left")
a.legend(fontsize=8.5, frameon=False, loc="upper left")
a.grid(alpha=0.25, which="both")
fig.tight_layout()
fig.savefig(arg.out + ".png", dpi=110)
print("scritto", arg.out + ".png")
