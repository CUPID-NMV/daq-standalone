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

f24, out = sys.argv[1], sys.argv[2]
fra = np.load(ROOT + "/measurements/carica_frascati_0005_p0-150_g150-600.npz")
sel = np.load(ROOT + "/measurements/frascati_0005_sel_3sipm_500k.npy")
lng = np.load(ROOT + "/measurements/carica_0011_testbeam.npz")
q20, q21 = lng["q_caen_ch20"], lng["q_caen_ch21"]
sig = {20: padme.sigma_robusta(q20[np.abs(q20) < 1500]), 21: padme.sigma_robusta(q21[np.abs(q21) < 1500])}
both = (q20 > 3 * sig[20]) & (q21 > 3 * sig[21])

# run da 24 ore: forme d'onda dei PMT, piedistallo 0-150, cancello 240-500
h, c, d, s = padme.leggi(f24, [20, 21], None)
_, _, dsi, _ = padme.leggi(f24, [16, 17, 18], None)
zs = dsi.astype(float) - dsi[:, :, :int(150 / (4 / 3))].mean(2, keepdims=True)
dt = 4 / 3
t = np.arange(d.shape[2]) * dt
z = -(d.astype(float) - d[:, :, t < 150].mean(2, keepdims=True))     # PMT negativi: verso positivo
g = (t >= 240) & (t < 500)
q = z[:, :, g].sum(2) * dt
# (evento, canale, colore, stile) delle tre linee
linee = [(17, 20, "#e8710a", "-"), (17, 21, "#c23b5a", "--"), (24, 21, "#c23b5a", "--")]

fig = plt.figure(figsize=(17, 14))
gs = fig.add_gridspec(3, 3, height_ratios=[1.25, 0.85, 0.6])
a = fig.add_subplot(gs[0, :])
b = np.linspace(-3000, 100000, 260)
W = lambda x: np.full(len(x), 1.0 / len(x))
for x, lab, cc, lw in ((fra["q_caen_ch24"][sel], "Frascati 0005 ch24, 3 SiPM > 500000 (%d ev.)" % sel.sum(), "#2a6fdb", 1.3),
                       (fra["q_caen_ch26"][sel], "Frascati 0005 ch26, 3 SiPM > 500000 (%d ev.)" % sel.sum(), "#1aa39a", 1.3),
                       (q20[both], "LNGS 0011 ch20, both PMT > 3σ_ped (%d ev.)" % both.sum(), "#e8710a", 1.6),
                       (q21[both], "LNGS 0011 ch21, both PMT > 3σ_ped (%d ev.)" % both.sum(), "#c23b5a", 1.6)):
    a.hist(x, b, weights=W(x), histtype="step", color=cc, lw=lw, label=lab)
for k, (e, ch, cc, ls) in enumerate(linee):
    v = q[e, ch - 20]
    a.axvline(v, color=cc, lw=2.4, ls=ls, label="(%s) LNGS 0000 muon, event %d, ch%d: %.0f ADC·ns" % ("abc"[k], e, ch, v))
    a.text(v, 0.056, "(%s)" % "abc"[k], color=cc, ha="center", va="bottom", fontsize=11, fontweight="bold")
a.set_xlim(-1000, 30000)
a.set_ylim(0, 0.062)
a.set_xlabel("PMT charge (absolute value of the integral of the negative pulse) [ADC·ns]   (1 pC = 204.8 ADC·ns)")
a.set_ylabel("fraction of events / bin (bin = %.0f ADC·ns), area = 1" % (b[1] - b[0]))
a.set_title("PMT charge spectra, area-normalised (pedestal 0-150 ns; gate 150-600 ns Frascati, 240-500 ns LNGS; "
            "CAEN correction only)", fontsize=10, loc="left")
a.legend(fontsize=8.5, frameon=False, loc="upper right")
a.grid(alpha=0.25)

for k, (e, ch, cc, ls) in enumerate(linee):
    a = fig.add_subplot(gs[1, k])
    asi = fig.add_subplot(gs[2, k], sharex=a)
    for j, (chs, cs) in enumerate(((16, "#2a6fdb"), (17, "#1aa39a"), (18, "#8a5cd6"))):
        asi.plot(t, zs[e, j], color=cs, lw=0.9, label="SiPM ch%d (peak %.0f)" % (chs, zs[e, j].max()))
    asi.axvspan(0, 150, color="#888", alpha=0.18)
    asi.axvspan(240, 500, color=cc, alpha=0.10)
    asi.axhline(4095 - np.median(dsi[e, :, :100]), color="k", ls=":", lw=0.8, label="ADC full scale")
    asi.set_xlabel("time [ns]")
    asi.set_ylabel("SiPM: ADC − pedestal [counts]")
    asi.legend(fontsize=7.5, frameon=False, loc="upper right")
    asi.grid(alpha=0.25)
    altro = 21 if ch == 20 else 20
    a.plot(t, -z[e, altro - 20], color="#bbb", lw=0.7, label="ch%d (other PMT, same event)" % altro)
    a.plot(t, -z[e, ch - 20], color=cc, lw=1.0, label="ch%d" % ch)
    a.axvspan(0, 150, color="#888", alpha=0.18, label="pedestal 0-150 ns")
    a.axvspan(240, 500, color=cc, alpha=0.10, label="gate 240-500 ns")
    a.axhline(0, color="#888", lw=0.6)
    a.set_xlim(0, 1350)
    a.tick_params(labelbottom=False)
    a.set_ylabel("PMT: ADC − pedestal [counts]")
    a.set_title("(%s) event %d, ch%d: charge %.0f ADC·ns, peak −%.0f counts"
                % ("abc"[k], e, ch, q[e, ch - 20], z[e, ch - 20].max()), fontsize=10, loc="left", color=cc)
    a.legend(fontsize=7.5, frameon=False, loc="lower right")
    a.grid(alpha=0.25)
fig.suptitle("Where the two LNGS muons (24 h run LNGS 0000, all three SiPM saturated) sit; below each line: PMT and SiPM waveforms of that event",
             fontsize=12)
fig.tight_layout()
fig.savefig(out, dpi=100)
