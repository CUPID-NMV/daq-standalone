# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
_sys.path.insert(0, _os.path.join(ROOT, "tools"))
import json, numpy as np, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
M = ROOT + "/measurements/"
serie = [("dcr_test__54-0V_0039_750Ms_10PT_100-850ns.json", "0039, 100-850 ns only (clean)", "#2a78d6", "-"),
         ("dcr_test__54-0V_0039_750Ms_10PT_intera.json", "0039, full window", "#eb6834", "--"),
         ("dcr_test__54-0V_0036_750Ms_50PT.json", "0036, full window (morning)", "#1baf7a", ":")]
D = [(json.load(open(M + f)), lab, col, ls) for f, lab, col, ls in serie]
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
plt.rcParams.update({"font.size": 10, "axes.edgecolor": INK2, "axes.labelcolor": INK, "xtick.color": INK2,
                     "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False})
fig, ax = plt.subplots(2, 3, figsize=(15, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1.3]})
fig.patch.set_facecolor("#fcfcfb")
for j, ch in enumerate(("16", "17", "18")):
    a, r = ax[0, j], ax[1, j]
    rif = None
    for d, lab, col, ls in D:
        o = d["canali"][ch]; pe = o["picchi"][0]
        x = np.array(o["soglie"]) / pe; y = np.array(o["dcr_poisson"], float); e = np.array(o["dcr_poisson_err"], float)
        a.plot(x, y, color=col, ls=ls, lw=2, label=lab)
        a.fill_between(x, y - e, y + e, color=col, alpha=0.15, lw=0)
        if rif is None: rif = (x, y)
        else: r.plot(x, y / np.interp(x, *rif), color=col, ls=ls, lw=2)
    a.set_yscale("log"); a.set_xlim(0.3, 3.2); a.set_ylim(1e4, 4e6)
    a.set_title("ch%s" % ch, color=INK, loc="left", fontweight="bold")
    for v in (0.5, 1.5, 2.5):
        a.axvline(v, color=INK2, lw=0.8, ls=(0, (2, 3))); r.axvline(v, color=INK2, lw=0.8, ls=(0, (2, 3)))
    o = D[0][0]["canali"][ch]; pe = o["picchi"][0]; x = np.array(o["soglie"]) / pe
    for v in (0.5, 1.5, 2.5):
        val = np.interp(v, x, np.array(o["dcr_poisson"], float))
        a.annotate("%s" % ("%.2f MHz" % (val / 1e6) if val > 1e6 else "%.0f kHz" % (val / 1e3)),
                   (v, val), xytext=(6, 6), textcoords="offset points", color=INK, fontsize=9)
    a.grid(color=GRID, lw=0.6, which="both"); r.grid(color=GRID, lw=0.6)
    r.axhline(1, color=INK2, lw=1); r.set_ylim(0.8, 1.4)
    r.set_xlabel("threshold [p.e.]  (1 p.e. = first peak of each run)")
    if j == 0:
        a.set_ylabel("dark count rate [Hz]  (Poisson, empty windows)")
        r.set_ylabel("ratio to clean")
ax[0, 0].legend(frameon=False, fontsize=9, loc="upper right")
fig.suptitle("SiPM dark count rate at 54 V vs threshold - software trigger, blind windows", color=INK, x=0.01, ha="left", fontsize=13)
fig.text(0.01, 0.005, "Bands: statistical error. At 1.5 p.e. and above the clean values are upper limits: pile-up of two 1 p.e. pulses is not corrected.",
         color=INK2, fontsize=9)
fig.tight_layout(rect=(0, 0.02, 1, 0.96))
fig.savefig(M + "dcr_confronto_0036_0039.png", dpi=120, facecolor=fig.get_facecolor())
print("ok")
