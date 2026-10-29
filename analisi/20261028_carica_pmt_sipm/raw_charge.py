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
h, c, d, s = padme.leggi(f, [16, 17, 18], None)
ns = d.shape[2]
dt = 4 / 3
t = np.arange(ns) * dt
k = np.arange(ns)
g = t >= 200
p = t < 150
L = g.sum() * dt
col = {16: "#2a6fdb", 17: "#1aa39a", 18: "#8a5cd6"}

fig, ax = plt.subplots(3, 2, figsize=(16, 11))
for riga, (j, ch) in zip(ax, enumerate(c)):
    y = d[:, j, :].astype(float)
    cel = (k[None, :] + s[:, ch // 8][:, None]) % 1024
    tab, _ = padme.mediana_per_gruppo(cel[:1000], y[:1000], 1024)
    yp = y - np.nan_to_num(tab - np.nanmedian(tab))[cel]
    raw_c = y[:, g].sum(1) * dt
    raw_p = yp[:, g].sum(1) * dt
    sub_p = (yp[:, g] - yp[:, p].mean(1, keepdims=True)).sum(1) * dt
    a = riga[0]
    lo, hi = np.percentile(np.concatenate([raw_c, raw_p]), [0.2, 99.8])
    b = np.linspace(lo, hi, 220)
    a.hist(raw_c, b, histtype="step", color="#888", lw=1.1, label="CAEN only")
    a.hist(raw_p, b, histtype="step", color=col[ch], lw=1.4, label="+ PADME (test-beam)")
    a.set_yscale("log")
    a.set_xlabel("Σ ADC × Δt in 200-1350 ns, NO baseline subtraction [ADC·ns]")
    a.set_ylabel("events / bin")
    a.set_title("ch%d  raw integral: median %.0f ADC·ns = %.1f counts × %.0f ns"
                % (ch, np.median(raw_p), np.median(raw_p) / L, L), fontsize=9.5, loc="left")
    a.legend(fontsize=8, frameon=False)
    a.grid(alpha=0.25, which="both")

    a = riga[1]
    r0 = raw_p - np.median(raw_p)
    s0 = sub_p - np.median(sub_p)
    lo, hi = np.percentile(np.concatenate([r0, s0]), [0.2, 99.8])
    b = np.linspace(lo, hi, 220)
    a.hist(r0, b, histtype="step", color=col[ch], lw=1.4,
           label="no baseline subtraction  σ_core = %.0f" % padme.sigma_robusta(r0))
    a.hist(s0, b, histtype="step", color="#e8710a", lw=1.2,
           label="baseline 0-150 ns subtracted  σ_core = %.0f" % padme.sigma_robusta(s0))
    a.set_yscale("log")
    a.set_xlabel("charge − its median [ADC·ns]  (both with PADME)")
    a.set_title("ch%d  same events, shifted to a common zero" % ch, fontsize=9.5, loc="left")
    a.legend(fontsize=8, frameon=False)
    a.grid(alpha=0.25, which="both")
    print(ch, "mediana grezza %.0f" % np.median(raw_p), "sigma grezza %.0f" % padme.sigma_robusta(r0),
          "sigma sottratta %.0f" % padme.sigma_robusta(s0),
          "rms livello 0-150 fra eventi %.2f" % yp[:, p].mean(1).std())
fig.suptitle("run 0011 SiPM — charge in 200 ns → end of trace, without baseline subtraction", fontsize=11)
fig.tight_layout()
fig.savefig(out, dpi=95)
