"""Verifica del MC dei gamma di Hall A (prototipo, geometria `single`).

    python3 analisi/20261029_compton_mc/verifica_mc_gamma.py MC_DIR OUT.png

1. Che spettro e' stato generato: E_init_MeV e' l'energia del primario
   ALL'INGRESSO IN ACQUA (0 se non ci entra), da confrontare con lo spettro
   scritto in gun_gammas_background.mac.
2. La fisica Compton: un elettrone fa luce Cherenkov in acqua (n = 1.33)
   solo sopra ~0.26 MeV di energia cinetica; l'elettrone Compton ha al
   massimo T_max = 2E^2 / (m_e + 2E), quindi un gamma sotto ~0.42 MeV non
   deve produrre Cherenkov da Compton. Sopra, i fotoni prodotti devono
   crescere con E.
3. Da quale processo viene la luce (colonne N_Cer_*).
"""
import os
import re
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import uproot

MC, OUT = sys.argv[1], sys.argv[2]
MAC = os.path.join(os.path.dirname(os.path.abspath(MC)), "gun_gammas_background.mac")
ME = 0.511
T_SOGLIA = 0.264         # MeV, soglia Cherenkov di un elettrone in acqua (n = 1.33)


def e_minima(t):
    """Energia del gamma il cui spigolo Compton vale t."""
    return 0.5 * (t + np.sqrt(t * t + 2 * ME * t))


f = uproot.open(os.path.join(MC, "sim_single_gamma_X0mm_Y0mm_Th0deg.root"))
ev = f["Eventi"].arrays(library="np")
E = ev["E_init_MeV"]
ncer = ev["N_Cerenkov_prod"]
proc = {k: ev[k] for k in ("N_Cer_primary", "N_Cer_compton", "N_Cer_pair", "N_Cer_deltaEM", "N_Cer_nuclear")}
hits = ev["Hits_L_Bot"] + ev["Hits_R_Bot"]
n = len(E)
dentro = E > 0
print("generati %d, entrati in acqua %d (%.1f%%), con Cherenkov %d, con fotoni ai PMT %d"
      % (n, dentro.sum(), 100 * dentro.mean(), (ncer > 0).sum(), (hits > 0).sum()))
Es = e_minima(T_SOGLIA)
sotto = dentro & (E < Es)
print("soglia: gamma sotto %.3f MeV non dovrebbero fare Cherenkov Compton: %d su %d ne fanno"
      % (Es, (sotto & (proc["N_Cer_compton"] > 0)).sum(), sotto.sum()))
tot = sum(v.sum() for v in proc.values())
for k, v in proc.items():
    print("  %-16s %6.1f%% dei fotoni Cherenkov" % (k, 100 * v.sum() / max(tot, 1)))

# spettro dichiarato nella macro
pts = []
for ln in open(MAC):
    m = re.match(r"\s*/gps/hist/point\s+([\d.eE+-]+)\s+([\d.eE+-]+)", ln)
    if m:
        pts.append((float(m.group(1)), float(m.group(2))))
pts = np.array(sorted(pts))

fig, ax = plt.subplots(2, 2, figsize=(17, 11))
a = ax[0, 0]
b = np.linspace(0, 3, 121)
h, _ = np.histogram(E[dentro], b)
a.step(0.5 * (b[1:] + b[:-1]), h / h.sum() / (b[1] - b[0]), where="mid", color="k", lw=1.4,
       label="MC: γ energy at water entry, %d γ" % dentro.sum())
xx = np.linspace(pts[0, 0], pts[-1, 0], 2000)
yy = np.interp(xx, pts[:, 0], pts[:, 1])
a.plot(xx, yy / np.trapezoid(yy, xx), color="#e8710a", lw=1.2,
       label="spectrum in gun_gammas_background.mac (linear interpolation, area 1)")
a.axvline(Es, color="#2a6fdb", ls="--", lw=1, label="E_γ below which no Compton e⁻ > Cherenkov threshold (%.2f MeV)" % Es)
a.set_yscale("log")
a.set_xlabel("E_γ [MeV]")
a.set_ylabel("probability density [1/MeV]")
a.set_title("1. generated spectrum vs the macro", fontsize=10, loc="left")
a.legend(fontsize=8, frameon=False)
a.grid(alpha=0.25, which="both")

a = ax[0, 1]
k = dentro & (ncer > 0)
a.scatter(E[k], ncer[k], s=5, color="k", lw=0, alpha=0.6, label="γ with Cherenkov photons: %d" % k.sum())
a.axvline(Es, color="#2a6fdb", ls="--", lw=1)
a.set_xlabel("E_γ at water entry [MeV]")
a.set_ylabel("Cherenkov photons produced in the event")
a.set_yscale("log")
a.set_title("2. Cherenkov light vs γ energy (nothing expected left of the dashed line)", fontsize=10, loc="left")
a.legend(fontsize=8, frameon=False)
a.grid(alpha=0.25, which="both")

a = ax[1, 0]
eb = np.linspace(0, 2.8, 29)
ec = 0.5 * (eb[1:] + eb[:-1])
nd = np.histogram(E[dentro], eb)[0]
nc = np.histogram(E[dentro & (ncer > 0)], eb)[0]
nh = np.histogram(E[dentro & (hits > 0)], eb)[0]
with np.errstate(invalid="ignore", divide="ignore"):
    a.plot(ec, nc / nd, "o-", color="k", label="fraction of γ entering water that make Cherenkov light")
    a.plot(ec, nh / nd, "s-", color="#e8710a", label="fraction with ≥ 1 photon at a PMT")
a.axvline(Es, color="#2a6fdb", ls="--", lw=1)
a.set_xlabel("E_γ at water entry [MeV]")
a.set_ylabel("fraction")
a.set_yscale("log")
a.set_title("3. probability vs energy (turn-on at the Cherenkov threshold)", fontsize=10, loc="left")
a.legend(fontsize=8, frameon=False)
a.grid(alpha=0.25, which="both")

a = ax[1, 1]
nomi = list(proc)
v = [100 * proc[x].sum() / max(tot, 1) for x in nomi]
a.bar(range(len(nomi)), v, color=["#888", "#e8710a", "#2a6fdb", "#1aa39a", "#7b2d8b"])
for i, x in enumerate(v):
    a.text(i, x, "%.1f%%" % x, ha="center", va="bottom", fontsize=9)
a.set_xticks(range(len(nomi)))
a.set_xticklabels([x.replace("N_Cer_", "") for x in nomi])
a.set_ylabel("% of Cherenkov photons")
a.set_title("4. which process makes the light", fontsize=10, loc="left")
a.grid(alpha=0.25, axis="y")
fig.suptitle("Check of the Hall A γ Monte Carlo (prototype geometry, %d γ generated)" % n, fontsize=11)
fig.tight_layout()
fig.savefig(OUT, dpi=100)
