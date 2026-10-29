"""Quanta radioattivita' nei moduli dei PMT servirebbe per l'eccesso LNGS, e
se ne riproduce la forma.

    python3 analisi/20261029_scint_fibre/sorgente_pmt.py MC_DIR TAG_K40 TAG_U238 TAG_TH232 HALLA.npz OUT.png

MC_DIR: la cartella con le produzioni delle macro gun_gammas_pmt_*.mac
(scritte da macro_sorgente_pmt.py); TAG_*: le loro etichette, per esempio
pmt_K40_s1750. HALLA.npz: i gamma di Hall A con la stessa resa della
scintillazione, digitalizzati con digit_mc.py.

Ogni produzione si digitalizza con analisi/20261030_digit_mc/digit_mc.py a
--rate 1: i rate che escono sono allora "per gamma emesso al secondo", cioe'
efficienze. Per Bq della catena (sommando i due moduli) si moltiplica per i
gamma per decadimento (measurements/sorgente_pmt_linee.json).

Attivita' richiesta = (classe C nei dati - classe C dei gamma di Hall A) /
classe C per Bq. Classe C: trigger, entrambi i PMT > 3 sigma, carica
massima >= 10000 ADC*ns.
"""
# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))

import json
import subprocess
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

MC_DIR, TAGS, F_HALLA, OUT = sys.argv[1], sys.argv[2:5], sys.argv[5], sys.argv[6]
MIS = _os.path.join(ROOT, "measurements")
DIGIT = _os.path.join(ROOT, "analisi", "20261030_digit_mc", "digit_mc.py")
T13 = 2367.313986
S = np.load(_os.path.join(MIS, "libreria_digit_0013.npz"))["sigma_q"]
LINEE = json.load(open(_os.path.join(MIS, "sorgente_pmt_linee.json")))


def classi(q, tr):
    sopra = q > 3 * S[None, :]
    both = tr & sopra.all(1)
    return {"both": both, "C": both & (q.max(1) >= 10000), "B": both & (q.max(1) < 7000),
            "one": tr & (sopra.sum(1) == 1), "trig": tr}


dati = np.load(_os.path.join(MIS, "carica_0013_testbeam.npz"))
qd = np.stack([dati["q_caen_ch20"], dati["q_caen_ch21"]], 1)
cd = classi(qd, np.ones(len(qd), bool))
rd = {k: v.sum() / T13 for k, v in cd.items()}
h = np.load(F_HALLA)
qh, ch = h["q"], classi(h["q"], h["trig"])
wh = float(h["peso_hz"])
rh = {k: v.sum() * wh for k, v in ch.items()}
manca = rd["C"] - rh["C"]
print("classe C: dati %.3f Hz, gamma di Hall A %.3f Hz -> mancano %.3f Hz" % (rd["C"], rh["C"], manca))

ris = {}
for tag in TAGS:
    catena = tag.split("_")[1]
    out = _os.path.join(MIS, "mc_digit_%s.npz" % tag)
    subprocess.run([sys.executable, DIGIT, MC_DIR, tag, out, "3", "--rate", "1"], check=True,
                   stdout=subprocess.DEVNULL)
    z = np.load(out)
    c = classi(z["q"], z["trig"])
    w = float(z["peso_hz"])                     # per gamma emesso
    g = LINEE[catena]["gamma_per_decadimento"]
    per_bq = {k: v.sum() * w * g for k, v in c.items()}
    a_req = manca / per_bq["C"] if per_bq["C"] > 0 else np.inf
    ris[catena] = dict(per_bq=per_bq, a_req=a_req, n_C=int(c["C"].sum()), q=z["q"], c=c, w=w * g,
                       ef=z["edep_fibre"])
    print("%-6s %8d gamma generati (x3 repliche): classe C %3d eventi; per Bq: C %.2e Hz, both %.2e, "
          "un PMT %.2e  ->  attivita' per i %.3f Hz mancanti: %.0f Bq (somma dei due moduli)"
          % (catena, int(z["n_gen"]), c["C"].sum(), per_bq["C"], per_bq["both"], per_bq["one"], manca, a_req))
    if np.isfinite(a_req):
        print("        con quell'attivita': un solo PMT %.2f Hz (dati %.2f), entrambi %.2f Hz (dati %.2f)"
              % (per_bq["one"] * a_req, rd["one"], per_bq["both"] * a_req, rd["both"]))

COL = {"K40": "#2a6fdb", "U238": "#e8710a", "Th232": "#1aa39a"}
b = np.linspace(-1000, 40000, 83)
bc = 0.5 * (b[1:] + b[:-1])
fig, ax = plt.subplots(1, 3, figsize=(21, 6.5))
a = ax[0]
hd = np.histogram(qd[cd["both"]].max(1), b)[0]
ok = hd > 0
a.errorbar(bc[ok], hd[ok] / T13, yerr=np.sqrt(hd[ok]) / T13, fmt="o", ms=3.5, color="k", lw=0.8,
           label="DATA LNGS 0013: %.3f Hz" % rd["both"])
a.step(bc, np.histogram(qh[ch["both"]].max(1), b)[0] * wh, where="mid", color="#d62728", lw=1.6,
       label="MC Hall A γ + fibre scintillation: %.3f Hz" % rh["both"])
for catena, r in ris.items():
    if not np.isfinite(r["a_req"]):
        continue
    hm = np.histogram(r["q"][r["c"]["both"]].max(1), b)[0] * r["w"] * r["a_req"]
    a.step(bc, hm, where="mid", color=COL[catena], lw=1.3,
           label="MC %s in the PMT modules, %.0f Bq (fills class C): %.3f Hz"
                 % (catena, r["a_req"], r["per_bq"]["both"] * r["a_req"]))
a.axvline(10000, color="#888", ls=":", lw=1)
a.set_yscale("log")
a.set_ylim(1e-5, 0.5)
a.ticklabel_format(axis="x", style="plain")
a.set_xlabel("charge of the brighter PMT [ADC·ns]")
a.set_ylabel("rate per bin [Hz] (bin = %.0f ADC·ns)" % (b[1] - b[0]))
a.set_title("both PMTs > 3σ: data vs Hall A γ and a source in the PMT modules", fontsize=10, loc="left")
a.legend(fontsize=7.5, frameon=False)
a.grid(alpha=0.25, which="both")

a = ax[1]
bl = np.linspace(-5, 5, 41)
lr = lambda q: np.log(np.clip(q[:, 0], 1, None) / np.clip(q[:, 1], 1, None))
a.hist(lr(qd[cd["C"]]), bl, histtype="step", color="k", lw=1.6, density=True,
       label="DATA class C (%d ev.)" % cd["C"].sum())
if ch["C"].any():
    a.hist(lr(qh[ch["C"]]), bl, histtype="step", color="#d62728", lw=1.3, density=True,
           label="MC Hall A γ, class C (%d)" % ch["C"].sum())
for catena, r in ris.items():
    if r["c"]["C"].any():
        a.hist(lr(r["q"][r["c"]["C"]]), bl, histtype="step", color=COL[catena], lw=1.3, density=True,
               label="MC %s in the PMT modules, class C (%d)" % (catena, r["c"]["C"].sum()))
a.set_xlabel("ln(Q20 / Q21)")
a.set_ylabel("normalised")
a.set_title("class C: charge asymmetry between the two PMTs", fontsize=10, loc="left")
a.legend(fontsize=7.5, frameon=False)
a.grid(alpha=0.25)

a = ax[2]
nomi = ["trig", "both", "C", "B", "one"]
etich = ["trigger", "both > 3σ", "C", "B", "one PMT"]
x = np.arange(len(nomi))
serie = [("DATA 0013", [rd[n] for n in nomi], "k"), ("Hall A γ (MC)", [rh[n] for n in nomi], "#d62728")]
serie += [("%s, %.0f Bq" % (c_, r["a_req"]), [r["per_bq"][n] * r["a_req"] for n in nomi], COL[c_])
          for c_, r in ris.items() if np.isfinite(r["a_req"])]
for k, (nome, v, col) in enumerate(serie):
    a.bar(x + (k - len(serie) / 2 + 0.5) * 0.16, v, 0.16, color=col, label=nome)
a.set_yscale("log")
a.set_ylim(1e-3, 50)
a.set_xticks(x)
a.set_xticklabels(etich)
a.set_ylabel("rate [Hz]")
a.set_title("rates per class, PMT-module source scaled to fill class C", fontsize=10, loc="left")
a.legend(fontsize=7.5, frameon=False)
a.grid(alpha=0.25, axis="y", which="both")
fig.suptitle("Could radioactivity in the PMT modules (on top of the fibre bundles) explain the LNGS excess? "
             "(MC with fibre scintillation, digitised like the data)", fontsize=11)
fig.tight_layout()
fig.savefig(OUT, dpi=95)
json.dump({c_: {"per_Bq_Hz": r["per_bq"], "attivita_richiesta_Bq": r["a_req"], "eventi_C_MC": r["n_C"]}
           for c_, r in ris.items()} | {"dati_Hz": rd, "hallA_Hz": rh, "mancante_C_Hz": manca},
          open(_os.path.join(MIS, "sorgente_pmt_risultati.json"), "w"), indent=1, default=float)
print("scritti", OUT, "e measurements/sorgente_pmt_risultati.json")
