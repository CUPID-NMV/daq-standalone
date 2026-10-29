"""Che cosa vuol dire il "flusso" di Haffke et al. (arXiv:1101.5298, NIM A 643
(2011) 36), e quindi con che rate vanno generati i gamma di Hall A nel MC.

    python3 analisi/20261029_normalizzazione_gamma/flusso_haffke.py HAFFKE.pdf OUT.png

HAFFKE.pdf: il PDF di arXiv:1101.5298 (non sta nel repository). Serve
pymupdf, oltre a numpy e matplotlib.

Perche': il MC genera i gamma su una scatola con rate (phi/4) * A, cioe'
tratta 0.35 cm^-2 s^-1 come flusso omnidirezionale (gamma che attraversano
una sfera di 1 cm^2 di sezione). L'articolo non dice come definisce il suo
flusso; dice solo che lo ricava dai conteggi del NaI 3"x3" "tenendo conto
dell'efficienza intrinseca del cristallo". Lo si ricava dai numeri: la Fig. 2
e' vettoriale, le curve si leggono dal PDF e si integrano, e il rapporto
conteggi / flusso della Tab. 1 e' l'area per cui e' stato diviso.

Per un campo isotropo phi, i gamma che entrano in un corpo convesso di
superficie S sono phi * S/4 (S/4 = area proiettata media). Se l'area che
viene fuori e' S/4 (68 cm^2), il valore della tabella e' phi; se e' S
(274 cm^2), e' la corrente per cm^2 di superficie, J = phi/4, e il MC va
generato con rate J * A.
"""
# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))

import json
import sys
import numpy as np
import pymupdf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PDF, OUT = sys.argv[1], sys.argv[2]
MIS = _os.path.join(ROOT, "measurements")
SOGLIA_KEV = 35.0                     # soglia del NaI dichiarata nell'articolo (involucro di Al)
R_CM, H_CM = 3.81, 7.62               # cristallo 3" x 3"
S_TOT = 2 * np.pi * R_CM**2 + 2 * np.pi * R_CM * H_CM
S_PROI = S_TOT / 4                    # area proiettata media (Cauchy), campo isotropo
FLUSSO_TAB = {"Hall A": (0.28, 0.02), "Hall B": (0.33, 0.03), "Interferometer": (0.42, 0.06)}
PHI_MC_VECCHIO = 0.35                 # quello che il MC usava come phi (rate = phi/4 * A)

pag = pymupdf.open(PDF)[2]           # pagina 3: Fig. 2 e Tab. 1
dr = pag.get_drawings()
fig2 = [g for g in dr if g["rect"].x1 < 300 and g["rect"].y1 < 240]
# tacche lunghe: x ogni 500 keV da 0; y a 1e-4, 1e-3, 1e-2, 1e-1 dal basso
xt = sorted({round(g["rect"].x0, 2) for g in fig2 if 225.5 < g["rect"].y0 < 226.5
             and g["rect"].width < 0.01 and len(g["items"]) == 1})
yt = sorted({round(g["rect"].y0, 2) for g in fig2 if abs(g["rect"].x0 - 69.5) < 0.2
             and 75 < g["rect"].x1 < 76 and g["rect"].height < 0.01})
assert len(xt) == 6 and len(yt) == 4, (xt, yt)
cal_x = np.polyfit(xt, 500.0 * np.arange(6), 1)
cal_y = np.polyfit(yt[::-1], -4.0 + np.arange(4), 1)

COLORI = {(0.0, 0.0, 0.0): "Hall A", (0.0, 0.6, 0.0): "Hall B", (1.0, 0.0, 0.0): "Interferometer",
          (0.0, 0.0, 1.0): "LVD CF"}
E = np.arange(0.0, 2800.0, 0.5)
spettri = {}
for g in fig2:
    if len(g["items"]) < 100:
        continue
    col = tuple(round(x, 2) for x in (g.get("color") or ()))
    nome = COLORI.get(col, str(col))
    if col == (0.0, 0.0, 0.0) and g.get("dashes") not in (None, "[] 0"):
        nome = "intrinsic"                                  # fondo del rivelatore, tratto-punto
    p = np.array([(q.x, q.y) for it in g["items"] if it[0] == "l" for q in (it[1], it[2])])
    e = np.polyval(cal_x, p[:, 0])
    r = 10 ** np.polyval(cal_y, p[:, 1])
    o = np.argsort(e, kind="stable")
    spettri[nome] = np.interp(E, e[o], r[o], left=0.0, right=0.0)

dE = E[1] - E[0]
ris = {}
for nome, (j, sj) in FLUSSO_TAB.items():
    n = spettri[nome][E >= SOGLIA_KEV].sum() * dE
    ris[nome] = dict(conteggi_s=n, flusso_tabella=j, err=sj, area_cm2=n / j)
    print("%-15s %.1f conteggi/s sopra %d keV, flusso in tabella %.2f -> conteggi/flusso = %.0f cm^2"
          % (nome, n, SOGLIA_KEV, j, n / j))
print("cristallo 3\"x3\": superficie %.1f cm^2, area proiettata media %.1f cm^2" % (S_TOT, S_PROI))
a_med = np.mean([r["area_cm2"] for r in ris.values()])
print("con efficienza intrinseca media 0.90-0.95: area %.0f-%.0f cm^2" % (a_med / 0.95, a_med / 0.90))

j_a = FLUSSO_TAB["Hall A"][0]
fatt = 4 * j_a / PHI_MC_VECCHIO
print("=> la tabella da' J = phi/4. Hall A: J = %.2f, phi = %.2f cm^-2 s^-1; "
      "rate del MC: J*A invece di %.2f/4*A, fattore %.2f" % (j_a, 4 * j_a, PHI_MC_VECCHIO, fatt))

fig, ax = plt.subplots(1, 2, figsize=(17, 6.3), width_ratios=[1.25, 1])
a = ax[0]
for nome, col, ls in (("Interferometer", "#d62728", "-"), ("Hall B", "#2ca02c", "--"), ("Hall A", "k", "-"),
                      ("LVD CF", "#1f5fd6", ":"), ("intrinsic", "#555", "-.")):
    a.plot(E, np.where(spettri[nome] > 0, spettri[nome], np.nan), ls, color=col, lw=0.9,
           label=nome + ("" if nome not in ris else ": %.1f counts/s above %d keV" % (ris[nome]["conteggi_s"], SOGLIA_KEV)))
a.axvline(SOGLIA_KEV, color="#888", ls=":", lw=1)
a.set_yscale("log")
a.set_ylim(1e-5, 1)
a.set_xlim(0, 2800)
a.set_xlabel("deposited energy [keV]")
a.set_ylabel("NaI 3\"x3\" count rate [keV$^{-1}$ s$^{-1}$]")
a.set_title("Haffke et al. 2011, Fig. 2, read from the vector PDF", fontsize=10, loc="left")
a.legend(fontsize=8.5, frameon=False)
a.grid(alpha=0.25, which="both")

a = ax[1]
jj = np.linspace(0, 0.5, 50)
a.fill_between(jj, 0.90 * S_TOT * jj, S_TOT * jj, color="#2a6fdb", alpha=0.18)
a.plot(jj, S_TOT * jj, color="#2a6fdb", lw=1.6,
       label="if the table gives J (γ per cm² of crystal SURFACE):\ncounts = ε·J·S,  S = %.0f cm²  (band: ε = 0.90-1)" % S_TOT)
a.fill_between(jj, 0.90 * S_PROI * jj, S_PROI * jj, color="#e8710a", alpha=0.18)
a.plot(jj, S_PROI * jj, color="#e8710a", lw=1.6,
       label="if the table gives φ (omnidirectional):\ncounts = ε·φ·S/4,  S/4 = %.0f cm²" % S_PROI)
for nome, r in ris.items():
    a.errorbar(r["flusso_tabella"], r["conteggi_s"], xerr=r["err"], fmt="o", color="k", ms=6, lw=1)
    a.annotate("%s  (%.0f cm²)" % (nome, r["area_cm2"]), (r["flusso_tabella"], r["conteggi_s"]),
               textcoords="offset points", xytext=(8, -12), fontsize=9)
a.set_xlim(0, 0.5)
a.set_ylim(0, 150)
a.set_xlabel("flux in Table 1 [s$^{-1}$ cm$^{-2}$]")
a.set_ylabel("integrated NaI count rate above %d keV [s$^{-1}$]" % SOGLIA_KEV)
a.set_title("which area turns their counts into their flux?", fontsize=10, loc="left")
a.legend(fontsize=8.5, frameon=False, loc="upper left")
a.grid(alpha=0.25)
fig.suptitle("The LNGS gamma 'flux' of arXiv:1101.5298 is per cm² of detector surface (φ/4): "
             "Hall A φ = %.2f γ cm$^{-2}$ s$^{-1}$, the MC generated %.1f times too few gammas" % (4 * j_a, fatt),
             fontsize=11)
fig.tight_layout()
fig.savefig(OUT, dpi=100)
json.dump({"per_luogo": ris, "soglia_keV": SOGLIA_KEV, "superficie_cristallo_cm2": S_TOT,
           "area_proiettata_media_cm2": S_PROI, "J_hallA": j_a, "phi_hallA": 4 * j_a,
           "phi_mc_vecchio": PHI_MC_VECCHIO, "fattore_mc": fatt,
           "nota": "la Tab. 1 di arXiv:1101.5298 da' gamma entranti per cm^2 di superficie del NaI (J = phi/4); "
                   "il MC va generato con rate J * A_gen, non phi/4 * A_gen con phi = valore della tabella"},
          open(_os.path.join(MIS, "flusso_haffke.json"), "w"), indent=1, default=float)
print("scritti", OUT, "e measurements/flusso_haffke.json")
