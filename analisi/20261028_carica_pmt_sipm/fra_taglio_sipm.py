# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
_sys.path.insert(0, _os.path.join(ROOT, "tools"))
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import padme

f, out = sys.argv[1], sys.argv[2]
SOGLIA = 500000.0
h, c, d, s = padme.leggi(f, [16, 17, 18], None, serve_sic=False)
ns = d.shape[2]
dt = 4 / 3
t = np.arange(ns) * dt
g = t >= 200
# Integrale GREZZO, nessun piedistallo sottratto, 200 ns -> fine traccia,
# come nell'analisi dei SiPM al Gran Sasso.
raw = np.stack([d[:, j, g].sum(1, dtype=np.int64) * dt for j in range(3)], axis=1)
sel = np.all(raw > SOGLIA, axis=1)
pmt = np.load(ROOT + "/measurements/carica_frascati_0005_p0-150_g150-600.npz")
print("eventi", len(sel), "passano", sel.sum())
for j, ch in enumerate([16, 17, 18]):
    print(ch, "mediana grezza %.0f" % np.median(raw[:, j]), "sopra soglia %d" % (raw[:, j] > SOGLIA).sum(),
          "base 0-150 mediana %.1f" % np.median(d[:, j, t < 150]))

fig = plt.figure(figsize=(18, 10))
gs = fig.add_gridspec(2, 6)
col = {16: "#2a6fdb", 17: "#1aa39a", 18: "#8a5cd6"}
for j, ch in enumerate([16, 17, 18]):
    a = fig.add_subplot(gs[0, 2 * j:2 * j + 2])
    b = np.linspace(np.percentile(raw[:, j], 0.05), np.percentile(raw[:, j], 99.95), 200)
    a.hist(raw[:, j], b, histtype="step", color=col[ch], lw=1.3, label="all %d events" % len(raw))
    a.hist(raw[sel, j], b, color="#d62728", alpha=0.6,
           label="all three SiPM > %.0f: %d events" % (SOGLIA, sel.sum()))
    a.axvline(SOGLIA, color="k", ls="--", lw=1)
    a.set_yscale("log")
    a.set_xlabel("raw Σ ADC × Δt, 200-1350 ns, nothing subtracted [ADC·ns]")
    a.set_ylabel("events / bin")
    a.set_title("ch%d  SiPM raw integral (median %.0f, %d events above %.0f)"
                % (ch, np.median(raw[:, j]), (raw[:, j] > SOGLIA).sum(), SOGLIA), fontsize=9.5, loc="left")
    a.legend(fontsize=8, frameon=False)
    a.grid(alpha=0.25, which="both")
bb = np.linspace(-3000, 100000, 260)
for k, ch in enumerate([24, 26]):
    a = fig.add_subplot(gs[1, 2 * k:2 * k + 2])
    q = pmt["q_caen_ch%d" % ch]
    a.hist(q, bb, histtype="step", color="#888", lw=1.2, label="all events: %d, median %.0f" % (len(q), np.median(q)))
    a.hist(q[sel], bb, histtype="step", color="#d62728", lw=1.6,
           label="3 SiPM > %.0f: %d, median %.0f" % (SOGLIA, sel.sum(), np.median(q[sel]) if sel.any() else 0))
    a.set_yscale("log")
    a.set_xlabel("PMT charge [ADC·ns], ped 0-150, gate 150-600 ns, CAEN only")
    a.set_ylabel("events / bin (not normalised)")
    a.set_title("ch%d  PMT charge spectrum" % ch, fontsize=9.5, loc="left")
    a.legend(fontsize=8, frameon=False)
    a.grid(alpha=0.25, which="both")
a = fig.add_subplot(gs[1, 4:6])
for j, ch in enumerate([16, 17, 18]):
    a.plot(t, d[sel, j].mean(0) if sel.any() else t * 0, color=col[ch], lw=0.9, label="ch%d selected" % ch)
    a.plot(t, d[:, j].mean(0), color=col[ch], lw=0.7, ls=":", label="ch%d all" % ch)
a.axvspan(200, t[-1], color="#e8710a", alpha=0.06)
a.set_xlabel("time [ns]")
a.set_ylabel("raw ADC [counts], mean")
a.set_title("SiPM mean waveforms: selected (solid) vs all (dotted)", fontsize=9.5, loc="left")
a.legend(fontsize=7, ncol=2)
a.grid(alpha=0.25)
fig.suptitle("Frascati run 0005 — PMT spectra for events where ALL THREE SiPM have raw integral > %.0f ADC·ns"
             % SOGLIA, fontsize=11)
fig.tight_layout()
fig.savefig(out, dpi=95)
np.save(ROOT + "/measurements/frascati_0005_sel_3sipm_500k.npy", sel)
