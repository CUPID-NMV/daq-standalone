"""Muoni attraverso il WC e i tre pannelli sotto di lui: quanti se ne aspettano
nella run da 24 ore, come li vedono i pannelli, e quanto la selezione coi
pannelli sposta la scala K tarata sui muoni di Frascati.

    python3 analisi/20261029_geometria_pannelli/geometria_muoni.py MC_DIR OUT.png

MC_DIR: la cartella data/ del progetto MC (per la macro gun_muons_lngs.mac, da
cui si leggono le distribuzioni angolari). Serve solo numpy/matplotlib.

Perche' un MC geometrico a parte: la ntupla del MC non salva la direzione del
muone primario, quindi i muoni simulati non si possono prolungare fino ai
pannelli. Qui si rifanno le stesse tracce rette: stesso piano di generazione
(6.5 x 6.5 m a z = 1.5 m), stesse distribuzioni di theta e phi campionate come
fa GPS (il peso del primo punto di ogni istogramma diventa un picco in 0: e' cio'
che rende il 25% dei muoni MC quasi verticali), e si controlla che la
lunghezza delle tracce in acqua torni con quella del MC.

Geometria (millimetri, origine al centro dell'acqua): acqua 960 x 360 x 100,
serbatoio 980 x 380 x 120; tre pannelli 1500 x 550 x 50 impilati subito sotto
il serbatoio (z da -60 a -210), lati lunghi paralleli. La posizione del WC sui
pannelli non e' nota: due casi, WC al centro e WC contro un angolo.

Un pannello attraversato per piu' di 15 mm satura il digitizer (un muone
pieno sono 50 mm e ~300 PE; la saturazione arriva a ~1/3); sopra 1 mm supera
la soglia del CFD (-20 mV, qualche PE). Il trigger della run da 24 ore e' la
maggioranza di 4 canali su 5: tre pannelli e un PMT, o due pannelli e due PMT.
"""
# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))

import json
import re
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

MC_DIR, OUT = sys.argv[1], sys.argv[2]
MIS = _os.path.join(ROOT, "measurements")
RATE_MC = 0.0127                     # Hz sul piano di generazione, come digit_mc.py
T24 = 84135.385                      # durata della run da 24 ore (dal suo .log)
N = 4_000_000
rng = np.random.default_rng(11)

ACQUA = ((-480, 480), (-180, 180), (-50, 50))
PANNELLI_Z = [(-110, -60), (-160, -110), (-210, -160)]
CASI = {"WC centred on the panels": ((-750, 750), (-275, 275)),
        "WC against a corner of the panels": ((-490, 1010), (-190, 360))}
P_SAT, P_CFD = 15.0, 1.0             # mm di scintillatore: satura / supera il CFD


def macro_hist(testo, tipo):
    """Punti di /gps/hist/point che seguono /gps/hist/type <tipo>."""
    blocco = testo.split("/gps/hist/type %s" % tipo)[1].split("/gps/hist/type")[0]
    return np.array([[float(a), float(b)] for a, b in re.findall(r"/gps/hist/point\s+(\S+)\s+(\S+)", blocco)])


def campiona_gps(punti, n):
    # come G4SPSAngDistribution: cumulativa che parte dal peso del PRIMO punto (picco
    # nel primo bordo), poi lineare dentro ogni intervallo
    cum = np.cumsum(punti[:, 1]) / punti[:, 1].sum()
    return np.interp(rng.random(n), cum, punti[:, 0])


def percorso(p, d, box):
    """Lunghezza della retta p + t d (t >= 0, |d| = 1) dentro una scatola."""
    with np.errstate(divide="ignore", invalid="ignore"):
        t_lo = np.full(len(p), -np.inf)
        t_hi = np.full(len(p), np.inf)
        for k, (lo, hi) in enumerate(box):
            a = (lo - p[:, k]) / d[:, k]
            b = (hi - p[:, k]) / d[:, k]
            # retta parallela all'asse k: dentro la fascia non vincola, fuori la manca
            # sempre (intervallo vuoto: tutti e due gli estremi a +inf)
            fermo = d[:, k] == 0
            dentro = (p[:, k] > lo) & (p[:, k] < hi)
            a = np.where(fermo, np.where(dentro, -np.inf, np.inf), a)
            b = np.where(fermo, np.inf, b)
            t_lo = np.maximum(t_lo, np.minimum(a, b))
            t_hi = np.minimum(t_hi, np.maximum(a, b))
    return np.clip(t_hi - np.maximum(t_lo, 0), 0, None)


def muoni(theta, phi, mezzo_lato):
    p = np.stack([rng.uniform(-mezzo_lato, mezzo_lato, len(theta)), rng.uniform(-mezzo_lato, mezzo_lato, len(theta)),
                  np.full(len(theta), 1500.0)], 1)
    d = -np.stack([np.sin(theta) * np.cos(phi), np.sin(theta) * np.sin(phi), np.cos(theta)], 1)
    return p, d


testo = open(_os.path.join(MC_DIR, "..", "gun_muons_lngs.mac")).read()
th = campiona_gps(macro_hist(testo, "theta"), N)
ph = campiona_gps(macro_hist(testo, "phi"), N)
p_l, d_l = muoni(th, ph, 3250.0)
# Frascati: muoni di superficie, I(theta) ~ cos^2 per angolo solido, quindi sul piano
# orizzontale pdf ~ cos^3 sin; piano piu' largo perche' arrivano piu' inclinati
u = rng.random(N) ** 0.25
p_f, d_f = muoni(np.arccos(u), rng.uniform(0, 2 * np.pi, N), 6000.0)

acqua_l = percorso(p_l, d_l, ACQUA)
acqua_f = percorso(p_f, d_f, ACQUA)

# --- validazione: lunghezza in acqua del toy contro il MC vero
mc = np.load(_os.path.join(MIS, "mc_digit_mu_lngs_s1750.npz"))
tl_mc = mc["trackl"][::int(mc["repliche"])]
tl_mc = tl_mc[tl_mc > 0]
print("validazione, muoni che attraversano l'acqua: toy %d, MC %d (eventi Geant4)" % ((acqua_l > 0).sum(), len(tl_mc)))
for nome, x in (("toy", acqua_l[acqua_l > 0]), ("MC ", tl_mc)):
    print("  %s: 99.5-100.5 mm %.1f%%, mediana %.0f mm, 10/90%% %.0f/%.0f mm"
          % (nome, 100 * ((x > 99.5) & (x < 100.5)).mean(), np.median(x), *np.percentile(x, [10, 90])))
r_acqua = (acqua_l > 0).sum() * RATE_MC / N
print("rate dei muoni che attraversano l'acqua: %.2e Hz (MC: %.2e) -> %.1f in %.0f s"
      % (r_acqua, len(tl_mc) * RATE_MC / int(mc["n_gen"]), r_acqua * T24, T24))

ris = {"validazione": {}, "casi": {}}
for caso, (bx, by) in CASI.items():
    pl = np.stack([percorso(p_l, d_l, (bx, by, z)) for z in PANNELLI_Z], 1)
    w = acqua_l > 0
    sat = (pl[w] > P_SAT).sum(1)
    cfd = (pl[w] > P_CFD).sum(1)
    trig = (cfd >= 2)                      # due pannelli + i due PMT (i muoni in acqua accendono tutti e due)
    k = RATE_MC / N * T24
    r = {"muoni_in_acqua": w.sum() * k, "trigger_4su5": trig.sum() * k,
         "con_3_saturi": (trig & (sat == 3)).sum() * k, "con_1_o_2_saturi": (trig & (sat >= 1) & (sat <= 2)).sum() * k,
         "senza_saturi": (trig & (sat == 0)).sum() * k}
    # muoni che passano i tre pannelli e il serbatoio ma non l'acqua (tipo evento 24)
    tank = percorso(p_l, d_l, ((-490, 490), (-190, 190), (-60, 60)))
    r["tre_pannelli_e_serbatoio_non_acqua"] = ((acqua_l == 0) & (tank > 0)
                                              & (np.stack([percorso(p_l, d_l, (bx, by, z)) for z in PANNELLI_Z], 1)
                                                 > P_SAT).all(1)).sum() * k
    ris["casi"][caso] = r
    print("%s, in %.1f h: muoni in acqua %.1f, col trigger %.1f; tre pannelli saturi %.1f, uno o due %.1f, nessuno %.2f;"
          " tre pannelli + serbatoio senza acqua %.2f"
          % (caso, T24 / 3600, r["muoni_in_acqua"], r["trigger_4su5"], r["con_3_saturi"], r["con_1_o_2_saturi"],
             r["senza_saturi"], r["tre_pannelli_e_serbatoio_non_acqua"]))

# --- scala K: i muoni di Frascati sono scelti coi tre pannelli
bins = np.concatenate([np.arange(0, 98, 4.0), [98, 99.5, 100.5, 102], np.arange(106, 402, 4.0)])
sel_mc = (mc["trackl"] > 0)
S = np.load(_os.path.join(MIS, "libreria_digit_0013.npz"))["sigma_q"]
both = sel_mc & (mc["q"][:, 0] > 3 * S[0]) & (mc["q"][:, 1] > 3 * S[1])


def mediana_pesata(x, w):
    o = np.argsort(x)
    c = np.cumsum(w[o])
    return x[o][np.searchsorted(c, 0.5 * c[-1])]


pA = np.histogram(acqua_l[acqua_l > 0], bins)[0].astype(float)
pA /= pA.sum()
idx = np.clip(np.digitize(mc["trackl"], bins) - 1, 0, len(bins) - 2)
q_both = mc["q"][both]
med_A = np.median(q_both.ravel())
kk = {}
for nome, (pp, dd, aa) in (("LNGS angles, three panels crossed", (p_l, d_l, acqua_l)),
                           ("Frascati (cos² θ), three panels crossed", (p_f, d_f, acqua_f))):
    for caso, (bx, by) in CASI.items():
        pl = np.stack([percorso(pp, dd, (bx, by, z)) for z in PANNELLI_Z], 1)
        s = (aa > 0) & (pl > P_SAT).all(1)
        pB = np.histogram(aa[s], bins)[0].astype(float)
        pB /= pB.sum()
        wgt = np.where(pA[idx] > 0, pB[idx] / np.where(pA[idx] > 0, pA[idx], 1), 0)[both]
        med_B = mediana_pesata(q_both.ravel(), np.repeat(wgt, 2))
        kk["%s | %s" % (nome, caso)] = dict(mediana_traccia_mm=float(np.median(aa[s])), mediana_carica=float(med_B),
                                            K_corretto=float(mc["k"]) * med_A / med_B, pB=pB)
        print("%-42s %-36s traccia mediana %.0f mm; carica MC mediana %.0f invece di %.0f -> K %.0f invece di %.0f"
              % (nome, caso, np.median(aa[s]), med_B, med_A, float(mc["k"]) * med_A / med_B, float(mc["k"])))

# --- grafico
fig, ax = plt.subplots(1, 3, figsize=(22, 6.6), width_ratios=[1.2, 1, 1.1])
a = ax[0]
bp = np.arange(0, 302, 4.0)
a.hist(tl_mc, bp, density=True, histtype="stepfilled", color="#bbb", label="Geant4 MC (LNGS muons in the water)")
a.hist(acqua_l[acqua_l > 0], bp, density=True, histtype="step", color="k", lw=1.4,
       label="geometric toy, same generator: validation")
cc = list(CASI)[0]
for nome, col in (("LNGS angles, three panels crossed", "#2a6fdb"), ("Frascati (cos² θ), three panels crossed", "#e8710a")):
    pp, dd, aa = (p_l, d_l, acqua_l) if nome.startswith("LNGS") else (p_f, d_f, acqua_f)
    bx, by = CASI[cc]
    pl = np.stack([percorso(pp, dd, (bx, by, z)) for z in PANNELLI_Z], 1)
    s = (aa > 0) & (pl > P_SAT).all(1)
    a.hist(aa[s], bp, density=True, histtype="step", color=col, lw=1.4, label=nome + " (WC centred)")
a.set_yscale("log")
a.set_xlabel("muon path length in the water [mm]")
a.set_ylabel("normalised")
a.set_title("path in the 10 cm of water: inclined tracks are longer", fontsize=10, loc="left")
a.legend(fontsize=8.5, frameon=False)
a.grid(alpha=0.25, which="both")

a = ax[1]
bt = np.array([0, 60, 90, 99.5, 100.5, 115, 130, 160, 300])
ct = []
for lo, hi in zip(bt[:-1], bt[1:]):
    m = both & (mc["trackl"] >= lo) & (mc["trackl"] < hi)
    if m.sum() >= 5:
        ct.append((0.5 * (lo + hi), np.median(mc["q"][m]), np.percentile(mc["q"][m], 25), np.percentile(mc["q"][m], 75), m.sum()))
ct = np.array(ct)
a.errorbar(ct[:, 0], ct[:, 1], yerr=[ct[:, 1] - ct[:, 2], ct[:, 3] - ct[:, 1]], fmt="o", color="#7b2d8b", ms=6,
           lw=1.2, capsize=3)
a.set_xlabel("muon path length in the water [mm]")
a.set_ylabel("charge per PMT [ADC·ns]  (median, 25-75%)")
a.ticklabel_format(axis="y", style="plain")
a.set_title("WC light vs path length (Geant4, digitised, K = %.0f)" % float(mc["k"]), fontsize=10, loc="left")
sec = a.secondary_yaxis("right", functions=(lambda x: x / float(mc["k"]), lambda x: x * float(mc["k"])))
sec.set_ylabel("photoelectrons per PMT")
a.grid(alpha=0.25)

a = ax[2]
a.axis("off")
righe = ["24-hour run (%.1f h, V812 4 of 5): expected muons" % (T24 / 3600), ""]
for caso, r in ris["casi"].items():
    righe += [caso + ":",
              "   in the water %.1f, firing the trigger %.1f" % (r["muoni_in_acqua"], r["trigger_4su5"]),
              "   panels saturated: three %.1f, one or two %.1f," % (r["con_3_saturi"], r["con_1_o_2_saturi"]),
              "   none %.2f" % r["senza_saturi"],
              ""]
righe += ["observed: 38 triggers; 2 with three panels saturated",
          "(one has light in one PMT only), 0 with one or two,",
          "36 with none (panels at 4-30% of a muon)", "",
          "K matching the Frascati muons (three panels crossed):"]
for k_, v in kk.items():
    ang, pos = k_.split(" | ")
    righe.append("   %-9s %-7s path %3.0f mm  ->  K %.0f" % ("LNGS" if ang.startswith("LNGS") else "Frascati",
                                                          "centred" if "centred" in pos else "corner",
                                                          v["mediana_traccia_mm"], v["K_corretto"]))
righe.append("   used so far: K = %.0f" % float(mc["k"]))
righe.append("   (all MC muons in the water, path %.0f mm)" % np.median(tl_mc))
a.text(0, 1, "\n".join(righe), va="top", ha="left", fontsize=9.2, family="monospace")
fig.suptitle("Muons through the WC and the three 150 × 55 × 5 cm panels below it (geometric toy with the Geant4 generator, "
             "WC position on the panels unknown: two cases)", fontsize=11)
fig.tight_layout()
fig.savefig(OUT, dpi=95)
json.dump({"casi": ris["casi"], "K": {k: {kk_: v_ for kk_, v_ in v.items() if kk_ != "pB"} for k, v in kk.items()},
           "K_usato": float(mc["k"]), "T24_s": T24, "P_SAT_mm": P_SAT, "P_CFD_mm": P_CFD},
          open(_os.path.join(MIS, "geometria_muoni.json"), "w"), indent=1, default=float)
print("scritti", OUT, "e measurements/geometria_muoni.json")
