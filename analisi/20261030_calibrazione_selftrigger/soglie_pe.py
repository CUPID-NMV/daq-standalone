"""Le soglie del self-trigger in p.e., canale per canale (scan del 2026-10-30).

    python3 analisi/20261030_calibrazione_selftrigger/soglie_pe.py OUT

Mette insieme tre misure, tutte in measurements/:
  - lo scan per canale (noise_scan_20261030_*_chNN.json): rate contro offset;
  - la calibrazione offset -> mV di calibra_selftrigger.py
    (calib_selftrigger_20261030_chNN.json), coi due stimatori: quantili dei
    rate e bordo basso (1%) delle ampiezze registrate;
  - l'ampiezza di un fotoelettrone: per i PMT dallo scan stesso
    (spe_pmt_scan_20261030.json), per i SiPM dalla misura del DCR della run
    0039 (26/10, 54 V, stessi canali, segnale filtrato a 50 MHz).

Per i PMT i due stimatori differiscono del ~15%: il punto usa i quantili, la
barra orizzontale arriva al bordo basso. Per i SiPM la soglia e' in p.e. di
AMPIEZZA: un impulso dei pannelli e' largo ~195 ns e i suoi fotoelettroni non
arrivano insieme, quindi non e' il numero di fotoelettroni dell'evento.

Scrive OUT.png e OUT.json (la tabella offset -> mV -> p.e.).
"""
import glob
import json
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
MIS = os.path.join(ROOT, "measurements")
OUT = sys.argv[1]
SIPM, PMT = (16, 17, 18), (20, 21)
COLORE = {16: "#2a78d6", 17: "#eb6834", 18: "#1baf7a", 20: "#eda100", 21: "#e87ba4"}
ATTUALE = {16: 600, 17: 600, 18: 600, 20: 50, 21: 50}     # offset della run di partenza
MIN_EVENTI = 50

# Un fotoelettrone in mV
spe = json.load(open(os.path.join(MIS, "spe_pmt_scan_20261030.json")))["canali"]
dcr = json.load(open(os.path.join(MIS, "dcr_test__54-0V_0039_750Ms_10PT_100-850ns.json")))
A_PE, A_PE_ERR = {}, {}
for ch in PMT:
    s = spe[str(ch)]
    A_PE[ch] = s["pe_ampiezza_mv"]
    A_PE_ERR[ch] = max(s["pe_ampiezza_err_mv"], s["pe_ampiezza_var_intervalli_mv"])
for ch in SIPM:
    A_PE[ch] = 2 * dcr["canali"][str(ch)]["soglie_pe"]["0.5"] * dcr["mv_per_conteggio"]
    A_PE_ERR[ch] = None


def retta(x, y):
    a, b = np.polyfit(np.asarray(x, float), np.asarray(y, float), 1)
    return float(a), float(b)


dati = {}
for ch in SIPM + PMT:
    sc = json.load(open(glob.glob(os.path.join(MIS, "noise_scan_20261030_1*_ch%d.json" % ch))[0]))
    ca = json.load(open(os.path.join(MIS, "calib_selftrigger_20261030_ch%d.json" % ch)))
    conteggi = {p["offset"]: p["conteggi"] for p in sc["punti"]}
    # Quantili: senza il passo di riferimento (li' la soglia e' il minimo del
    # campione, non una misura) e senza i passi con pochi eventi, dove il
    # quantile cade su una manciata di impulsi.
    q = [(p["offset"], p["soglia_mv"]) for p in ca["punti"]
         if p["frazione"] < 0.98 and conteggi.get(p["offset"], 0) >= MIN_EVENTI]
    e = [(p["offset"], p["q1_mv"]) for p in ca["verifica_bordo_basso"]]
    dati[ch] = {"scan": sc, "q": q, "e": e, "fit_q": retta(*zip(*q)), "fit_e": retta(*zip(*e))}

fig, ax = plt.subplots(2, 2, figsize=(16, 11.5))
tabella = {}
for col, gruppo, nome in ((0, SIPM, "SiPM"), (1, PMT, "PMT (inverted)")):
    a = ax[0, col]
    xmax = max(p["offset"] for ch in gruppo for p in dati[ch]["scan"]["punti"])
    xx = np.linspace(0, xmax, 50)
    for ch in gruppo:
        D, c = dati[ch], COLORE[ch]
        (aq, bq), (ae, be) = D["fit_q"], D["fit_e"]
        a.plot(*zip(*D["q"]), "o", color=c, ms=7, mec="white", mew=1)
        a.plot(*zip(*D["e"]), "s", color=c, ms=6, mfc="none", mew=1.3)
        a.plot(xx, aq * xx + bq, "-", color=c, lw=1.6,
               label="ch%d  rate quantiles: %.3f mV/offset %+.1f mV" % (ch, aq, bq))
        a.plot(xx, ae * xx + be, "--", color=c, lw=1.3,
               label="ch%d  lower edge (1%%): %.3f mV/offset %+.1f mV" % (ch, ae, be))
    a.set_xlabel("self-trigger threshold [offset counts]")
    a.set_ylabel("threshold [mV at the digitizer input]")
    a.set_title("%s: offset → mV, measured on the recorded waveforms (filled: rate quantiles, "
                "open: lower edge)" % nome, fontsize=10, loc="left")
    a.set_xlim(0, xmax * 1.03)
    a.set_ylim(bottom=0)
    a.grid(alpha=0.3)
    a.legend(fontsize=8.5, frameon=False, loc="upper left")
    # Le rette si usano anche oltre l'ultimo punto misurato: si dice.
    ultimo = max(o for ch in gruppo for o, _ in dati[ch]["q"] + dati[ch]["e"])
    if ultimo < 0.9 * xmax:
        a.axvspan(ultimo, xmax * 1.03, color="#888", alpha=0.08, lw=0)
        a.text(0.97, 0.04, "points: steps with >= %d events\nshaded: lines extrapolated\n"
               "beyond the last of them (offset %g)" % (MIN_EVENTI, ultimo),
               transform=a.transAxes, ha="right", va="bottom", fontsize=9, color="#444")

    a = ax[1, col]
    for ch in gruppo:
        D, c = dati[ch], COLORE[ch]
        (aq, bq), (ae, be) = D["fit_q"], D["fit_e"]
        off = np.array([p["offset"] for p in D["scan"]["punti"]], float)
        rate = np.array([p["rate"] for p in D["scan"]["punti"]], float)
        n = np.array([p["conteggi"] for p in D["scan"]["punti"]], float)
        durata = float(D["scan"]["secondi_per_punto"])
        pq = (aq * off + bq) / A_PE[ch]
        pe = (ae * off + be) / A_PE[ch]
        ok = n > 0
        a.errorbar(pq[ok], rate[ok], yerr=np.sqrt(n[ok]) / durata,
                   xerr=[np.clip(pq[ok] - pe[ok], 0, None), np.clip(pe[ok] - pq[ok], 0, None)],
                   fmt="o-", color=c, ms=7, mec="white", mew=1, lw=1.6, capsize=0,
                   label="ch%d  (1 p.e. = %.2f mV%s)" % (ch, A_PE[ch],
                                                       " ± %.1f" % A_PE_ERR[ch] if A_PE_ERR[ch] else ""))
        if (~ok).any():
            a.plot(pq[~ok], np.full((~ok).sum(), 1.0 / durata), "v", color=c, ms=8, alpha=0.8,
                   label="ch%d  no events in %g s (upper limit)" % (ch, durata))
        i = int(np.argmin(abs(off - ATTUALE[ch])))
        a.plot(pq[i], rate[i], "o", ms=15, mfc="none", mec=c, mew=1.8)
        tabella[str(ch)] = {
            "pe_mv": A_PE[ch], "pe_mv_err": A_PE_ERR[ch],
            "mv_per_offset_quantili": aq, "intercetta_quantili_mv": bq,
            "mv_per_offset_bordo": ae, "intercetta_bordo_mv": be,
            "punti": [{"offset": float(o), "rate_hz": float(r), "soglia_mv_quantili": float(aq * o + bq),
                       "soglia_mv_bordo": float(ae * o + be), "soglia_pe_quantili": float(x1),
                       "soglia_pe_bordo": float(x2)}
                      for o, r, x1, x2 in zip(off, rate, pq, pe)]}
    a.set_yscale("log")
    a.set_ylim(1e-2, 2e3)
    if col == 1:
        a.set_xscale("log")
        a.set_xlim(0.15, 12)
        for v in (0.5, 1, 2):
            a.axvline(v, color="#999", ls=":", lw=1)
        a.set_xlabel("threshold [photoelectrons of amplitude]  (point: rate quantiles; bar: lower edge)")
        a.set_title("PMT: self-trigger rate of each PMT alone vs threshold in PE "
                    "(ring: offset 50, the setting before the scan)", fontsize=10, loc="left")
    else:
        a.set_xlim(0, 190)
        a.set_xlabel("threshold [photoelectrons of amplitude, 1 p.e. from the DCR of run 0039]")
        a.set_title("SiPM: self-trigger rate of each panel alone vs threshold in p.e. of amplitude "
                    "(ring: offset 600)", fontsize=10, loc="left")
    a.set_ylabel("rate of that channel alone [Hz]")
    a.grid(alpha=0.3, which="both")
    a.legend(fontsize=9, frameon=False, loc="upper right")

fig.suptitle("Self-trigger thresholds in photoelectrons — per-channel scan of 2026-10-30 "
             "(run LNGS_scan_54-0V_0000, 750 MS/s, 60 s per step, other channels parked)", fontsize=12)
fig.tight_layout()
fig.savefig(OUT + ".png", dpi=100)
json.dump(tabella, open(OUT + ".json", "w"), indent=2)

for ch in SIPM + PMT:
    T = tabella[str(ch)]
    print("ch%d  1 p.e. = %.2f mV   quantili %.3f mV/off %+.1f   bordo %.3f mV/off %+.1f"
          % (ch, T["pe_mv"], T["mv_per_offset_quantili"], T["intercetta_quantili_mv"],
             T["mv_per_offset_bordo"], T["intercetta_bordo_mv"]))
    for p in T["punti"]:
        print("     offset %5g  rate %8.2f Hz   %6.1f / %6.1f mV   %6.2f / %6.2f p.e."
              % (p["offset"], p["rate_hz"], p["soglia_mv_quantili"], p["soglia_mv_bordo"],
                 p["soglia_pe_quantili"], p["soglia_pe_bordo"]))
print("scritto", OUT + ".png", OUT + ".json")
