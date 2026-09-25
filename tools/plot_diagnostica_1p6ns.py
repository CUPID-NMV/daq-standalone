#!/usr/bin/env python3
"""Figure esplicative per la calibrazione della soglia a 1.6 ns.

  1. efficienza_1p6ns.png   la curva di efficienza e il confronto con quella
                            che darebbe la sola dispersione delle ampiezze
  2. popolazioni_1p6ns.png  come si distinguono segnale, rumore e artefatti
                            del V1742, e perche' ch9 e' il discriminante
  3. deriva_1p6ns.png       deriva del piedistallo, e rumore contro artefatti
                            in funzione della soglia

Va lanciato sul PC DAQ, dove stanno i file di dati.

    python3 tools/plot_diagnostica_1p6ns.py
"""

import json
import os
import sys

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daqio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "plots")
CAL = os.path.join(ROOT, "measurements", "calibrazione_1p6ns_20260925.json")
RUM = os.path.join(ROOT, "measurements", "rumore_artefatti_20260925.json")

# Il pre-scan della run 0104 e' a offset 4 con purezza verificata al 99.9%:
# e' il campione di segnale puro, e non dipende dai confini dei passi dello
# scan, che in quella run non sono ricostruibili con certezza.
PRESCAN = (0, 6000)
COINC_NS = 2.0            # tolleranza per dire che due canali hanno lo stesso picco
SOGLIA_CH9 = -20          # conteggi: ch9 ha rumore 2.97, questo e' ben sopra


def leggi(nome, primi=None, ultimi=None):
    path = os.path.join(ROOT, "data", nome)
    if not os.path.exists(path):
        path += ".gz"
    try:
        f, tmp = daqio.open_file(path, live=False)
    except daqio.DaqFileError:
        # Una run non chiusa in modo pulito lascia il flag di scrittura nel
        # file: in lettura normale HDF5 lo rifiuta, in SWMR no.
        f, tmp = daqio.open_file(path, live=True)
    try:
        hdr = daqio.read_header(f)
        w = f["events/waveforms"]
        ns, nch = hdr["SamplesPerChannel"], len(hdr["ChannelList"])
        dt = hdr["SamplingTime"] * 1e9
        a, z = (primi if primi else (w.shape[0] - ultimi, w.shape[0]))
        a, z = max(a, 0), min(z, w.shape[0])
        d = np.asarray(w[a:z, :]).reshape(z - a, nch, ns).astype(np.float32)
    finally:
        f.close()
        daqio._cleanup(tmp)
    dc = np.median(d, axis=2)
    s = d - dc[:, :, None]
    return dict(amp=s.min(axis=2), pos=np.argmin(s, axis=2) * dt, dc=dc, dt=dt)


def artefatti(ev):
    """Stesso picco su entrambi i canali: e' il difetto noto del V1742."""
    vicino = np.abs(ev["pos"][:, 0] - ev["pos"][:, 1]) <= COINC_NS
    return (ev["amp"][:, 1] < SOGLIA_CH9) & vicino


# ----------------------------------------------------------------------
def fig_efficienza(cal, seg, out):
    d = np.array([p["distanza"] for p in cal["punti"]])
    eff = np.array([p["efficienza"] for p in cal["punti"]])
    n = np.array([p["rate_hz"] for p in cal["punti"]]) * 30
    err = eff * np.sqrt(1.0 / np.maximum(n, 1))

    fig, ax = plt.subplots(figsize=(7.8, 5.0))
    ax.errorbar(d, 100 * eff, yerr=100 * err, fmt="o", ms=9, capsize=4, lw=1.8,
                color="#1f77b4", zorder=5,
                label="misura, normalizzata ai %g Hz del generatore"
                      % cal["rate_generatore_hz"])

    # previsione con la SOLA dispersione delle ampiezze, tarata perche' il 50%
    # cada dove lo dice la misura: e' il turn-off piu' largo compatibile con la
    # distribuzione osservata, e resta un gradino
    reali = seg["amp"][:, 0][seg["amp"][:, 0] < -90]
    d50 = cal["distanza_50pc"]
    k = abs(np.median(reali)) / d50
    g = np.linspace(0, d.max() + 1.5, 400)
    ax.plot(g, 100 * np.array([(np.abs(reali) > k * x).mean() for x in g]),
            lw=2.2, ls="--", color="#c0392b",
            label="previsione dalla sola dispersione delle\nampiezze (5-95%%: %.0f/%.0f cnt)"
                  % (np.percentile(reali, 5), np.percentile(reali, 95)))

    ax.axvline(d50, color="#666", lw=1, ls=":")
    ax.annotate("50%% a distanza %.2f\n%.2f mV per offset\nattenuazione %.1f"
                % (d50, cal["mv_per_offset"], cal["attenuazione"]),
                xy=(d50, 50), xytext=(d50 + 0.7, 68), fontsize=9.5, color="#333",
                arrowprops=dict(arrowstyle="->", color="#666"))

    ax.axvspan(0, 3.2, color="#c0392b", alpha=.09)
    ax.annotate("escluso: qui il rate e' rumore\ne artefatti, non segnale\n"
                "(dimostrato dalla run senza segnale)",
                xy=(1.55, 22), ha="center", fontsize=8.5, color="#8a2020")

    ax.set_xlabel("distanza soglia-piedistallo  [conteggi, Transparent Mode]")
    ax.set_ylabel("efficienza del self-trigger  [%]")
    ax.set_title("Impulso da %.1f ns, %.1f mV: calibrazione alla larghezza dei PMT"
                 % (cal["larghezza_ns"], abs(cal["ampiezza_mv"])), fontsize=12)
    ax.set_xlim(0, d.max() + 1.5)
    ax.set_ylim(-2, 105)
    ax.grid(alpha=.3)
    ax.legend(fontsize=8.5, loc="upper right")
    salva(fig, out)


def fig_popolazioni(seg, rum, out):
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.8))

    ax = axes[0]
    art = artefatti(rum)
    ax.scatter(rum["amp"][~art, 0], rum["amp"][~art, 1], s=8, alpha=.35,
               color="#7f8c8d", edgecolors="none", label="rumore")
    ax.scatter(rum["amp"][art, 0], rum["amp"][art, 1], s=14, alpha=.7,
               color="#c0392b", edgecolors="none", label="artefatto V1742")
    lim = [-75, 5]
    ax.plot(lim, lim, lw=1.2, color="#333", ls="--", label="ampiezze uguali")
    ax.set_xlim(*lim); ax.set_ylim(*lim)
    ax.set_xlabel("ampiezza ch8  [conteggi]")
    ax.set_ylabel("ampiezza ch9  [conteggi]")
    ax.set_title("Run senza segnale: l'artefatto sta sulla diagonale", fontsize=11)
    ax.legend(fontsize=8.5, loc="upper left")
    ax.grid(alpha=.3)
    ax.annotate("stessa ampiezza sui due canali:\n$-52$ e $-54$ cnt, rapporto 1.02",
                xy=(-52, -54), xytext=(-70, -28), fontsize=8.5, color="#8a2020",
                arrowprops=dict(arrowstyle="->", color="#c0392b"))

    ax = axes[1]
    a, p = seg["amp"][:, 0], seg["pos"][:, 0]
    ax.scatter(p, a, s=8, alpha=.4, color="#1f77b4", edgecolors="none")
    ax.axvspan(40, 60, color="#2ca02c", alpha=.10)
    ax.set_xlim(0, 410)
    ax.set_xlabel("posizione del minimo nella finestra  [ns]")
    ax.set_ylabel("ampiezza ch8  [conteggi]")
    ax.set_title("Con segnale: impulsi veri, tutti a 50 ns e a $-126$ cnt",
                 fontsize=11)
    ax.grid(alpha=.3)

    fig.suptitle("Segnale, rumore e artefatto si separano senza ambiguita'",
                 fontsize=12)
    salva(fig, out)


def fig_deriva(seg, rum, rumjson, out):
    fig, axes = plt.subplots(2, 1, figsize=(8.0, 6.6))

    ax = axes[0]
    ev = np.arange(len(seg["dc"]))
    lisci = np.column_stack([liscia(seg["dc"][:, i]) for i in range(2)])
    for i, ch in enumerate((8, 9)):
        ax.plot(ev, lisci[:, i] - lisci[0, i], lw=1.8,
                label="ch%d%s" % (ch, "  (generatore)" if i == 0 else "  (scollegato)"))
    diff = (lisci[:, 0] - lisci[0, 0]) - (lisci[:, 1] - lisci[0, 1])
    ax.plot(ev, diff, lw=2.0, color="#c0392b",
            label="differenza: %.2f cnt" % (diff.max() - diff.min()))
    ax.axhline(0, color="#999", lw=.8)
    ax.set_xlabel("evento")
    ax.set_ylabel("deriva del livello DC  [conteggi]\n(media mobile su 101 eventi)")
    ax.set_title("Run 0104: deriva sotto il conteggio, e comune ai due canali",
                 fontsize=11)
    ax.legend(fontsize=8.5, ncol=3)
    ax.grid(alpha=.3)

    ax = axes[1]
    pts = rumjson["punti"]
    d = np.array([p["distanza"] for p in pts])
    tot = np.array([p["rate_totale"] for p in pts])
    fa = np.array([p["frazione_artefatti"] for p in pts])
    ax.semilogy(d, tot * (1 - fa), "o-", color="#7f8c8d", ms=8, label="rumore")
    ax.semilogy(d, tot * fa, "s-", color="#c0392b", ms=8, label="artefatti V1742")
    ax.axvline(3.2, color="#2ca02c", lw=1.4, ls=":")
    ax.annotate("l'artefatto smette di\npassare la soglia qui:\n~3.2 conteggi",
                xy=(3.2, 6), xytext=(3.45, 40), fontsize=8.5, color="#1a6b1a")
    ax.set_xlabel("distanza soglia-piedistallo  [conteggi]")
    ax.set_ylabel("rate  [Hz]")
    ax.set_title("Run senza segnale: le due componenti del fondo", fontsize=11)
    ax.legend(fontsize=9)
    ax.grid(alpha=.3, which="both")
    salva(fig, out)


def liscia(x, n=101):
    """Media mobile con clipping: isola la deriva dalla dispersione."""
    n = min(n, max(3, len(x) // 6))
    c = np.median(x)
    x = np.clip(x, c - 8, c + 8)
    pad = np.pad(x, n // 2, mode="edge")
    return np.convolve(pad, np.ones(n) / n, mode="valid")[:len(x)]


def salva(fig, nome):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, nome)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    print("grafico:", path)


def main():
    cal = json.load(open(CAL))
    rumjson = json.load(open(RUM))
    seg = leggi(cal["run"], primi=PRESCAN)
    rum = leggi(rumjson["run"].replace(".gz", ""), ultimi=4000)
    print("eventi letti: %d con segnale, %d senza" % (len(seg["amp"]), len(rum["amp"])))
    fig_efficienza(cal, seg, "efficienza_1p6ns.png")
    fig_popolazioni(seg, rum, "popolazioni_1p6ns.png")
    fig_deriva(seg, rum, rumjson, "deriva_1p6ns.png")


if __name__ == "__main__":
    main()
