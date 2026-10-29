# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
_sys.path.insert(0, _os.path.join(ROOT, "tools"))
import sys, numpy as np
sys.path.insert(0, ROOT + "/tools")
import daqio
path, n, live = sys.argv[1], int(sys.argv[2]), sys.argv[3] == "live"
kw = dict(last=n, live=True) if live else dict(max_events=n)
h, d = daqio.load(path, channels=[20, 21], **kw)
dt = float(h["SamplingTime"]) * 1e9
d = d[:, :, 5:-20]
y = d - np.median(d, axis=2, keepdims=True)
ne, nc, ns = y.shape
# passa-alto: toglie la media mobile su ~12 ns, resta l oscillazione veloce
L = 9; k = np.ones(L) / L
hp = y - np.apply_along_axis(lambda v: np.convolve(v, k, "same"), 2, y)
# l impulso del PMT: si maschera attorno al minimo se e grande
amp = -y.min(axis=2); pos = y.argmin(axis=2)
W = 32
mask = np.ones_like(hp, bool)
for e in range(ne):
    for c in range(nc):
        if False:
            mask[e, c, max(0, pos[e, c] - 40):pos[e, c] + 80] = False
r2 = np.where(mask, hp**2, np.nan)
nw = ns // W
rr = np.sqrt(np.nanmean(r2[:, :, :nw * W].reshape(ne, nc, nw, W), axis=3))
s0 = np.nanmedian(rr, axis=(0, 2))
burst = rr > 3 * s0[None, :, None]
print("%s  eventi %d, %.3g ns/campione, finestre da %d campioni" % (path.split("/")[-1], ne, dt, W))
for c, ch in enumerate((20, 21)):
    b = burst[:, c].any(axis=1)
    print("  ch%d: rumore veloce tipico %.2f conteggi; eventi con raffica %d (%.1f%%); impulso >40 in %.1f%% degli eventi" % (ch, s0[c], b.sum(), 100 * b.mean(), 100 * (amp[:, c] > 40).mean()))
both = (burst[:, 0] & burst[:, 1]).any(axis=1)
b0, b1 = burst[:, 0].any(axis=1), burst[:, 1].any(axis=1)
print("  raffica su entrambi nella stessa finestra: %d eventi; atteso se indipendenti ~%.1f" % (both.sum(), b0.mean() * b1.mean() * ne * burst.shape[2] ** 0 ))
if both.any():
    w = np.where(burst[:, 0] & burst[:, 1])[1]
    print("  posizione delle raffiche comuni [ns]: mediana %.0f, quartili %.0f-%.0f" % tuple(np.percentile((w + 0.5) * W * dt, [50, 25, 75])))
    # frequenza dominante nelle finestre comuni
    e, wi = np.where(burst[:, 0] & burst[:, 1])
    seg = np.array([hp[ee, 0, ww * W - 32 + W // 2: ww * W + 32 + W // 2] for ee, ww in zip(e, wi) if ww * W - 32 + W // 2 >= 0 and ww * W + 32 + W // 2 <= ns])
    if len(seg):
        P = (np.abs(np.fft.rfft(seg * np.hanning(seg.shape[1]), axis=1)) ** 2).mean(0)
        f = np.fft.rfftfreq(seg.shape[1], dt * 1e-9) / 1e6
        print("  frequenza dominante nelle raffiche: %.0f MHz" % f[1:][P[1:].argmax()])
    print("  ampiezza tipica nelle raffiche: %.1f conteggi rms" % np.nanmedian(rr[:, 0][burst[:, 0]]))
quieti = (amp < 20).all(axis=1)
print("  eventi senza impulso su nessuno dei due PMT: %d" % quieti.sum())
bq = burst[quieti]
both = (bq[:, 0] & bq[:, 1])
print("  fra questi, raffica comune: %d (%.1f%%)" % (both.any(axis=1).sum(), 100 * both.any(axis=1).mean()))
if both.any():
    w = np.where(both)[1]
    print("  posizione [ns]: mediana %.0f, quartili %.0f-%.0f" % tuple(np.percentile((w + 0.5) * W * dt, [50, 25, 75])))
