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
h, c, d, s = padme.leggi(f, [20, 21], None)
ns = d.shape[2]
dt = 4 / 3
t = np.arange(ns) * dt
ip = int(150 / dt)
k = np.arange(ns)


def tab(y, sic, n_ev, i1):
    cel = (k[None, :i1] + sic[:n_ev, None]) % 1024
    m, _ = padme.mediana_per_gruppo(cel, y[:n_ev, :i1], 1024)
    return m


def rms_ped(y, sic, E):
    """rms nel tratto 0-150 dopo la correzione, piedistallo per evento tolto"""
    yc = y - E[(k[None, :] + sic[:, None]) % 1024]
    z = yc[:, :ip] - yc[:, :ip].mean(1, keepdims=True)
    return np.median(z.std(1))


fig, ax = plt.subplots(2, 2, figsize=(15, 7.5))
for j, ch in enumerate(c):
    y = d[:, j, :].astype(float)
    sg = s[:, ch // 8]        # SIC del gruppo del canale (gruppo 2)
    s0 = s[:, 0]              # SIC del gruppo 0, l'unica nel formato CYGNO
    tb = tab(y, s0, 1000, ns)           # ricetta del test beam
    tbg = tab(y, sg, 1000, ns)          # test beam ma con la SIC del gruppo giusto
    mio = tab(y, sg, len(y), ip)        # quello usato finora
    for E in (tb, tbg, mio):
        E -= np.nanmedian(E)
    a = ax[0, j]
    a.plot(tbg, lw=0.9, color="#e8710a", label="test-beam recipe (whole trace, first 1000 ev.), group-2 SIC")
    a.plot(mio, lw=0.9, color="#2a6fdb", alpha=0.7, label="mine (0-150 ns, all 5337 ev.), group-2 SIC")
    a.set_title("ch%d  per-cell table  (rms of difference: %.2f counts)"
                % (ch, np.nanstd(tbg - mio)), fontsize=10, loc="left")
    a.set_xlabel("DRS4 cell")
    a.set_ylabel("offset [counts]")
    a.legend(fontsize=7.5)
    a.grid(alpha=0.25)
    a = ax[1, j]
    nomi = ["CAEN only", "same, group-0 SIC\n(=65535, group not read:\nin fact per READ INDEX)", "test beam\n(group-2 SIC)", "mine"]
    v = [rms_ped(y, sg, np.zeros(1024)), rms_ped(y, s0, np.nan_to_num(tb)),
         rms_ped(y, sg, np.nan_to_num(tbg)), rms_ped(y, sg, np.nan_to_num(mio))]
    b = a.bar(range(4), v, color=["#888", "#c9a227", "#e8710a", "#2a6fdb"])
    for x, val in enumerate(v):
        a.text(x, val, "%.2f" % val, ha="center", va="bottom", fontsize=9)
    a.set_xticks(range(4))
    a.set_xticklabels(nomi, fontsize=8.5)
    a.set_ylim(0, max(v) * 1.2)
    a.set_ylabel("pedestal rms 0-150 ns [counts]")
    a.set_title("ch%d  noise after correction, all events" % ch, fontsize=10, loc="left")
    a.grid(alpha=0.25, axis="y")
    print(ch, ["%.3f" % x for x in v], "diff rms %.3f" % np.nanstd(tbg - mio),
          "SIC g0 vs g2: uguali in %.0f%%" % (100 * np.mean(s0 == sg)))
fig.suptitle("run 0011 PMTs: PADME as in wclib.py (test beam) vs as applied here", fontsize=11)
fig.tight_layout()
fig.savefig(out, dpi=105)
