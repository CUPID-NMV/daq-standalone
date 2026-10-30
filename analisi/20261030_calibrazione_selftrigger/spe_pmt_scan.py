"""Il fotoelettrone dei PMT invertiti, dallo scan del self-trigger del 2026-10-30.

    python3 analisi/20261030_calibrazione_selftrigger/spe_pmt_scan.py RUN.h5 SCAN_ch20.json SCAN_ch21.json OUT

Al passo piu' basso dello scan di un PMT (offset 10, gli altri canali alla
soglia massima) la DAQ registra ~25 Hz di impulsi di QUEL PMT, sotto la soglia
di un fotoelettrone: quasi tutti impulsi propri del PMT da un fotoelettrone.
La loro AMPIEZZA da' la scala in PE della soglia, perche' il comparatore del
self-trigger guarda l'ampiezza; la loro CARICA si confronta con K, la scala
usata finora (1647-1726 ADC*ns/PE), per vedere se l'inverter ha cambiato
qualcosa.

Scrive OUT.png, OUT.json e OUT.npz (ampiezze, cariche e istanti di ogni evento,
per la sovrapposizione dei PMT singoli). Gira sul PC DAQ, dove sta la run.
"""
import json
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import daqio                                           # noqa: E402

MV = 1000.0 / 4096.0
RUN, OUT = sys.argv[1], sys.argv[-1]
SCANS = sys.argv[2:-1]
# Carica attorno al massimo: l'impulso e' largo ~11 ns, la finestra ne prende
# la coda senza arrivare alla parte di verso opposto dell'accoppiamento in
# alternata (~800 ns dopo, vedi CLAUDE.md).
PRIMA_NS, DOPO_NS = 10.0, 30.0

fig, ax = plt.subplots(2, len(SCANS), figsize=(7.5 * len(SCANS), 9.5))
ax = np.atleast_2d(ax).reshape(2, len(SCANS))
risultati, salva = {}, {}
for j, sf in enumerate(SCANS):
    d = json.load(open(sf))
    ch = int(d["canali"][0])
    p = min(d["punti"], key=lambda q: q["offset"])
    hdr, w = daqio.load(RUN, max_events=p["eventi_a"], channels=[ch], live=False)
    w = w[p["eventi_da"]:, 0, :].astype(float)
    dt = float(hdr["SamplingTime"]) * 1e9
    n = len(w)
    durata = n / p["rate"]
    base = np.median(w, axis=1)
    c = w - base[:, None]
    # Il massimo si cerca vicino all'istante del trigger, non su tutta la
    # traccia: in fondo alla finestra il V1742 mette spesso un picco spurio,
    # che su un impulso piccolo vincerebbe.
    i_tutti = c.argmax(axis=1)
    moda = int(np.bincount(i_tutti).argmax())
    lo, hi = max(moda - int(50 / dt), 0), min(moda + int(50 / dt), c.shape[1])
    i_pk = lo + c[:, lo:hi].argmax(axis=1)
    amp = c[np.arange(n), i_pk] * MV
    a0, a1 = int(PRIMA_NS / dt), int(DOPO_NS / dt)
    q = np.array([c[k, max(i - a0, 0):i + a1].sum() * dt for k, i in enumerate(i_pk)])

    # Picco del fotoelettrone: gaussiana attorno alla moda dell'istogramma.
    h, b = np.histogram(amp, np.arange(0, 200, 2.0))
    bc = 0.5 * (b[1:] + b[:-1])
    m = bc[h.argmax()]
    sel = (amp > 0.6 * m) & (amp < 1.4 * m)
    mu, sg = float(np.mean(amp[sel])), float(np.std(amp[sel]))
    for _ in range(5):
        sel = (amp > mu - 1.5 * sg) & (amp < mu + 1.5 * sg)
        mu, sg = float(np.mean(amp[sel])), float(np.std(amp[sel]))
    hq, bq = np.histogram(q, np.arange(-500, 8000, 150.0))
    bqc = 0.5 * (bq[1:] + bq[:-1])
    mq = bqc[hq.argmax()]
    selq = (q > 0.6 * mq) & (q < 1.4 * mq)
    muq, sgq = float(np.mean(q[selq])), float(np.std(q[selq]))
    for _ in range(5):
        selq = (q > muq - 1.5 * sgq) & (q < muq + 1.5 * sgq)
        muq, sgq = float(np.mean(q[selq])), float(np.std(q[selq]))
    print("ch%d: offset %g, %d eventi in %.0f s (%.1f Hz), impulsi a %.0f ns" %
          (ch, p["offset"], n, durata, p["rate"], moda * dt))
    print("      ampiezza del PE: %.1f mV (sigma %.1f, %.0f conteggi); carica del PE: %.0f ADC*ns (sigma %.0f)"
          % (mu, sg, mu / MV, muq, sgq))
    risultati[str(ch)] = {"offset": p["offset"], "eventi": n, "durata_s": durata,
                          "istante_ns": moda * dt, "pe_ampiezza_mv": mu,
                          "pe_ampiezza_sigma_mv": sg, "pe_carica_adcns": muq,
                          "pe_carica_sigma_adcns": sgq,
                          "finestra_carica_ns": [-PRIMA_NS, DOPO_NS]}
    salva["amp_ch%d" % ch], salva["q_ch%d" % ch] = amp, q
    salva["t_ch%d" % ch] = i_pk * dt
    salva["durata_ch%d" % ch] = durata

    a = ax[0, j]
    a.stairs(h / durata, b, color="#2a6fdb", lw=1.6, label="ch%d, scan step at offset %g: %d events, %.1f Hz"
             % (ch, p["offset"], n, p["rate"]))
    xx = np.linspace(mu - 3 * sg, mu + 3 * sg, 200)
    a.plot(xx, sel.sum() / durata * 2.0 / (sg * np.sqrt(2 * np.pi)) / 0.866 *
           np.exp(-0.5 * ((xx - mu) / sg) ** 2), color="#e8710a", lw=1.4,
           label="1 PE: %.1f mV (σ %.1f) = %.0f counts" % (mu, sg, mu / MV))
    a.set_xlabel("pulse amplitude [mV at the digitizer input]")
    a.set_ylabel("rate per 2 mV [Hz]")
    a.set_title("PMT ch%d (inverted, positive pulses): amplitude of the pulses that fired the self-trigger"
                % ch, fontsize=10, loc="left")
    a.legend(fontsize=9, frameon=False)
    a.grid(alpha=0.25)
    a = ax[1, j]
    a.stairs(hq / durata, bq, color="#2a6fdb", lw=1.6, label="charge in %g..+%g ns around the peak"
             % (-PRIMA_NS, DOPO_NS))
    a.axvline(muq, color="#e8710a", lw=1.4, label="1 PE: %.0f ADC·ns (σ %.0f)" % (muq, sgq))
    for k_, nome, col in ((1647, "K from the muon MC (fibre scint. 3500/MeV)", "#888"),
                          (1726, "K from the muon MC (no fibre scint.)", "#bbb")):
        a.axvline(k_, color=col, ls=":", lw=1.2, label="%s: %d" % (nome, k_))
    a.set_xlabel("pulse charge [ADC·ns]")
    a.set_ylabel("rate per 150 ADC·ns [Hz]")
    a.legend(fontsize=9, frameon=False)
    a.grid(alpha=0.25)

fig.suptitle("PMT single photoelectron from the self-trigger scan of 2026-10-30 "
             "(run LNGS_scan_54-0V_0000, 750 MS/s, lowest step, other channels parked)",
             fontsize=11)
fig.tight_layout()
fig.savefig(OUT + ".png", dpi=100)
json.dump({"run": os.path.basename(RUN), "scan": [os.path.basename(s) for s in SCANS],
           "canali": risultati}, open(OUT + ".json", "w"), indent=2)
np.savez_compressed(OUT + ".npz", **salva)
print("scritto", OUT + ".png", OUT + ".json", OUT + ".npz")
