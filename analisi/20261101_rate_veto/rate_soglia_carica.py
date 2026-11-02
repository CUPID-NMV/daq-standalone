"""Rate con una soglia in carica a 1.8 PE, letta sui dati della 0013 dove la riga della
soglia incontra i dati, ed estrapolata al veto completo col rapporto delle aree d'acqua.

    python3 analisi/20261101_rate_veto/rate_soglia_carica.py [--soglia-pe 1.8] [--out PREFISSO]

Nella figura della sovrapposizione (sovrapposizione_0011_0013.py --piedistallo) l'asse
verticale e' una DENSITA', Hz per 100 ADC*ns: l'altezza dei punti dove li incontra la riga
della soglia e' la rate degli eventi in una finestra larga 0.06 PE attorno alla soglia, e
cambia con la larghezza della finestra. La rate con quella soglia e' l'area a destra della
riga. In piu' quei punti sono divisi per la durata di 0011 + 0013 insieme, mentre a 1.8 PE
conta solo la 0013 (la 0011 ha la soglia del trigger a 3.5-4.3 PE): li' sono sei volte piu'
bassi del vero.

Qui, sulla sola 0013: sopra gli spettri di carica come nella sovrapposizione, con l'area a
destra della soglia; sotto la rate integrale R(>= x), cioe' gli eventi al secondo con almeno
un PMT sopra x. Su questa curva il punto dove la riga incontra i dati E' la rate con quella
soglia; l'asse a destra la moltiplica per il rapporto fra l'area d'acqua del veto e quella
del prototipo (rate_veto_20261101.json, scritto da rate_veto.py).

La soglia del self-trigger della 0013 e' in AMPIEZZA (offset 100), quella di qui in carica:
sotto il ginocchio la curva integrale e' piatta perche' la run non ha registrato gli eventi
sotto la sua soglia, non perche' non ci siano. La rate sotto ~1.8 PE da questa run non si
misura.
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
ap.add_argument("--soglia-pe", type=float, default=1.8)
ap.add_argument("--out", default=_os.path.join(ROOT, "measurements", "rate_veto_20261101_soglia"))
arg = ap.parse_args()
MIS = _os.path.join(ROOT, "measurements")

SOGLIE = json.load(open(_os.path.join(MIS, "soglie_trigger_lngs.json")))
VETO = json.load(open(_os.path.join(MIS, "rate_veto_20261101.json")))
RAP = VETO["rapporto_aree"]
# K dei muoni e dei gamma digitalizzati con le fibre a 3500 fotoni/MeV: lo stesso della
# sovrapposizione, cosi' la riga cade sugli stessi ADC*ns
K = float(np.load(_os.path.join(MIS, "mc_digit_gamma_righe_s3500.npz"))["k"])
X = arg.soglia_pe * K

RUN = {"0013": "LNGS__54-0V_0013_750Ms_55PT", "0011": "LNGS__54-0V_0011_750Ms_55PT"}
T = {r: SOGLIE[n]["durata_s"] for r, n in RUN.items()}
TT = sum(T.values())
q = {}
for r in RUN:
    z = np.load(_os.path.join(MIS, "carica_%s_testbeam.npz" % r))
    q[r] = np.stack([z["q_caen_ch20"], z["q_caen_ch21"]], 1)
q13, T13 = q["0013"], T["0013"]
voci = {"ch20": q13[:, 0], "ch21": q13[:, 1], "uno": q13.max(1), "entrambi": q13.min(1)}


def sopra(v, x):
    n = int((v >= x).sum())
    return n, n / T13, np.sqrt(n) / T13


# ------------------------------------------------------------------ numeri
# stessi bin della sovrapposizione: 100 ADC*ns fino a 2000, poi 10 per decade, in Hz per 100 ADC*ns
b = np.concatenate([np.arange(-2000, 2000, 100.0), np.logspace(np.log10(2000), np.log10(200000), 21)])
dens = np.diff(b) / 100.0
i = np.searchsorted(b, X) - 1
altezze = {}
for j, ch in ((0, 20), (1, 21)):
    fig_ = np.histogram(np.concatenate([q["0011"][:, j], q13[:, j]]), b)[0] / TT / dens
    solo = np.histogram(q13[:, j], b)[0] / T13 / dens
    altezze["ch%d" % ch] = dict(bin_della_riga=[b[i], b[i + 1]], come_nella_figura=[fig_[i], fig_[i + 1]],
                                solo_0013=[solo[i], solo[i + 1]])
    print("ch%d, altezza dei punti attorno alla riga [Hz per 100 ADC*ns]: nella figura %.1e / %.1e, "
          "sulla sola 0013 %.1e / %.1e" % (ch, fig_[i], fig_[i + 1], solo[i], solo[i + 1]))

ris = {}
for pe in sorted({arg.soglia_pe, 2.0, 2.2, 3.0}):
    ris["%.1f" % pe] = {k: dict(zip(("eventi", "hz", "err_hz"), sopra(v, pe * K))) for k, v in voci.items()}
r0 = ris["%.1f" % arg.soglia_pe]
n_uno, R_uno, e_uno = r0["uno"]["eventi"], r0["uno"]["hz"], r0["uno"]["err_hz"]
print("0013: %d eventi in %.0f s = %.3f Hz di trigger" % (len(q13), T13, len(q13) / T13))
for pe, d in ris.items():
    print("  >= %s PE: almeno un PMT %.3f Hz (%d eventi), ch20 %.3f, ch21 %.3f, entrambi %.3f  ->  veto x%.1f: %.0f Hz"
          % (pe, d["uno"]["hz"], d["uno"]["eventi"], d["ch20"]["hz"], d["ch21"]["hz"], d["entrambi"]["hz"], RAP,
             d["uno"]["hz"] * RAP))

json.dump(dict(run=RUN["0013"], durata_s=T13, eventi=len(q13), k_adcns_per_pe=K, soglia_pe=arg.soglia_pe,
               soglia_adcns=X, rapporto_aree=RAP, area_veto_m2=VETO["area_acqua_m2"],
               area_prototipo_m2=VETO["area_prototipo_m2"], rate_sopra_soglia=ris,
               veto_hz=R_uno * RAP, veto_err_hz=e_uno * RAP, altezze_alla_riga=altezze),
          open(arg.out + ".json", "w"), indent=1)
print("scritto", arg.out + ".json")

# ------------------------------------------------------------------ figura
ROSSO, BLU = "#d62728", "#1f4e9c"
fig, ax = plt.subplots(2, 1, figsize=(13, 10.5), height_ratios=[1, 1.15], sharex=True)
bc = np.where(b[:-1] > 0, np.sqrt(np.clip(b[1:] * b[:-1], 1e-12, None)), 0.5 * (b[1:] + b[:-1]))

a = ax[0]
hu = np.histogram(voci["uno"], b)[0] / T13 / dens
# l'area da colorare e' quella a destra della riga: il bin che la contiene si taglia alla riga
bs = np.concatenate([[X], b[b > X]])
hs = hu[np.searchsorted(b, X) - 1:]
a.stairs(hs, bs, fill=True, color=BLU, alpha=0.18, lw=0,
         label="area right of the line = %.2f Hz: the rate with a %.1f PE threshold" % (R_uno, arg.soglia_pe))
# i bin vuoti sotto la soglia (35 eventi in tutto) come buchi, non come righe fino al fondo
a.stairs(np.where(hu > 0, hu, np.nan), b, baseline=None, color=BLU, lw=2.0,
         label="larger of the two PMT charges, all %d events" % len(q13))
for j, ch, col, mk in ((0, 20, "#3c3c3c", "o"), (1, 21, "#8c8c8c", "s")):
    h = np.histogram(q13[:, j], b)[0]
    ok = h > 0
    a.errorbar(bc[ok], h[ok] / T13 / dens[ok], yerr=np.sqrt(h[ok]) / T13 / dens[ok], fmt=mk, ms=3.2, color=col,
               mfc=col if ch == 20 else "white", lw=0.7, zorder=4,
               label="PMT ch%d alone, all events (as in the overlap figure, peak at 0 = no light in that PMT)" % ch)
a.axvline(X, color=ROSSO, lw=3.0, zorder=2.5,
          label="threshold %.1f PE = %.0f ADC·ns" % (arg.soglia_pe, X))
a.set_yscale("log")
a.set_ylim(1e-5, 30)
a.set_ylabel("rate per 100 ADC·ns [Hz]")
a.set_title("run 0013 alone (self-trigger offset 100 on both PMTs, %.0f min, %d events): the height of a point is a "
            "density, the rate above a threshold is an area" % (T13 / 60, len(q13)), fontsize=9.5, loc="left")
a.legend(fontsize=9, frameon=False, loc="upper right")
a.grid(alpha=0.25, which="both")

a = ax[1]
xg = np.concatenate([np.linspace(-1500, 2000, 351), np.logspace(np.log10(2000), np.log10(200000), 801)[1:]])
for nome, v, col, lw, ls, lab in (("uno", voci["uno"], BLU, 2.4, "-", "at least one PMT above x"),
                                  ("ch20", voci["ch20"], "#3c3c3c", 1.3, "-", "PMT ch20 above x"),
                                  ("ch21", voci["ch21"], "#8c8c8c", 1.3, "--", "PMT ch21 above x")):
    vs = np.sort(v)
    n = len(vs) - np.searchsorted(vs, xg, side="left")
    R = n / T13
    if nome == "uno":
        a.fill_between(xg, np.clip(R - np.sqrt(n) / T13, 1e-9, None), R + np.sqrt(n) / T13, color=col, alpha=0.2, lw=0)
    a.plot(xg, np.where(n > 0, R, np.nan), color=col, lw=lw, ls=ls, label=lab)
a.axvline(X, color=ROSSO, lw=3.0, zorder=2.5)
a.plot([X], [R_uno], "o", ms=11, mfc="white", mec=ROSSO, mew=2.5, zorder=6)
a.annotate("at least one PMT ≥ %.1f PE:\n%.2f ± %.2f Hz on the prototype (%.3f m² of water)\n"
           "× %.1f (water area ratio) = %.0f ± %.0f Hz on the full veto (%.1f m²)"
           % (arg.soglia_pe, R_uno, e_uno, VETO["area_prototipo_m2"], RAP, R_uno * RAP, e_uno * RAP,
              VETO["area_acqua_m2"]),
           (X, R_uno), xytext=(9000, 0.9), fontsize=10.5, va="center",
           bbox=dict(facecolor="white", edgecolor=ROSSO, alpha=0.95),
           arrowprops=dict(arrowstyle="-", color=ROSSO, lw=1.2))
a.text(-1300, 1.5e-3, "below the knee the curve is flat because run 0013 did not record\n"
       "events under its own trigger threshold (on amplitude, offset 100)", fontsize=8.5, color="#555")
a.set_yscale("log")
a.set_ylim(1e-3, 20)
a.set_ylabel("rate above x on the prototype [Hz]")
sec = a.secondary_yaxis("right", functions=(lambda y: y * RAP, lambda y: y / RAP))
sec.set_ylabel("× %.1f: rate on the full veto [Hz]" % RAP)
a.set_title("integral rate R(≥ x): where the %.1f PE line meets the data, the height IS the rate with that threshold"
            % arg.soglia_pe, fontsize=9.5, loc="left")
a.legend(fontsize=9, frameon=False, loc="lower left", bbox_to_anchor=(0.0, 0.12))
a.grid(alpha=0.25, which="both")
a.set_xscale("symlog", linthresh=2000, linscale=0.6)
a.set_xlim(-1500, 200000)
a.set_xlabel("charge x [ADC·ns]  (gate 240-500 ns, pedestal 0-150 ns; 1 PE = %.0f ADC·ns)" % K)
fig.tight_layout()
fig.savefig(arg.out + ".png", dpi=110)
print("scritto", arg.out + ".png")
