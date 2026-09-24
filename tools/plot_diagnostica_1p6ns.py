#!/usr/bin/env python3
"""Figure esplicative per lo scan in soglia con impulso da 1.6 ns.

Tre grafici, ognuno risponde a una domanda rimasta aperta:

  1. efficienza.png   il turn-off e' molto piu' largo di quanto la dispersione
                      delle ampiezze giustifichi
  2. popolazioni.png  quali trigger sono segnale e quali rumore, e come si
                      distinguono dalla posizione dell'impulso nella finestra
  3. deriva.png       la deriva del piedistallo e' comune ai due canali, e
                      spiega la perdita di purezza a soglia bassa

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

SOGLIA_IMPULSO = -40      # conteggi: sotto questa ampiezza l'evento ha un impulso
FINESTRA_50NS = (40, 60)  # ns: dove il self-trigger mette l'impulso
ATT_CONTINUA = 1.89       # attenuazione del Transparent Mode in continua


def carica(meta):
    """Ampiezza, posizione e livello DC per ogni evento della run."""
    path = os.path.join(ROOT, "data", meta["run"])
    if not os.path.exists(path):
        path += ".gz"
    f, tmp = daqio.open_file(path, live=not path.endswith(".gz"))
    try:
        hdr = daqio.read_header(f)
        w = f["events/waveforms"]
        n, ns = w.shape[0], hdr["SamplesPerChannel"]
        nch = len(hdr["ChannelList"])
        amp = np.empty(n); pos = np.empty(n)
        dc = np.empty((n, nch))
        for a in range(0, n, 2000):                     # a blocchi: il file e' grande
            z = min(a + 2000, n)
            d = np.asarray(w[a:z, :]).reshape(z - a, nch, ns).astype(np.float32)
            m = np.median(d, axis=2)
            dc[a:z] = m
            s = d[:, 0, :] - m[:, 0][:, None]
            amp[a:z] = s.min(axis=1)
            pos[a:z] = np.argmin(s, axis=1) * hdr["SamplingTime"] * 1e9
    finally:
        f.close()
        daqio._cleanup(tmp)
    return amp, pos, dc, list(hdr["ChannelList"])


def buoni(amp, pos):
    """Eventi con un impulso vero, riconosciuto dalla posizione nella finestra."""
    return (amp < SOGLIA_IMPULSO) & (pos > FINESTRA_50NS[0]) & (pos < FINESTRA_50NS[1])


# ----------------------------------------------------------------------
def fig_efficienza(meta, amp, pos, out):
    """Misura contro la previsione che tiene conto solo della dispersione."""
    p = [x for x in meta["punti"] if not x.get("limite_superiore")]
    lim = [x for x in meta["punti"] if x.get("limite_superiore")]
    rg = meta["rate_generatore_hz"]

    d = np.array([x["distanza"] for x in p])
    eff = np.array([x["rate_hz"] * x["purezza"] / rg for x in p])
    # errore: statistico sul conteggio + 10% sulla purezza dove e' stata stimata
    nev = np.array([x["rate_hz"] * meta["secondi_per_punto"] for x in p])
    err = eff * np.sqrt(1.0 / np.maximum(nev, 1) + (0.10 * (1 - np.array(
        [x["purezza"] for x in p])))**2)

    dmax = max([x["distanza"] for x in meta["punti"]])
    fig, ax = plt.subplots(figsize=(7.6, 5.0))
    ax.errorbar(d, 100 * eff, yerr=100 * err, fmt="o", ms=9, capsize=4, lw=1.8,
                color="#1f77b4", zorder=5, label="misura (corretta per la purezza)")
    for x in lim:
        ax.annotate("", xy=(x["distanza"], 0.4), xytext=(x["distanza"], 4.5),
                    arrowprops=dict(arrowstyle="-|>", color="#1f77b4", lw=1.4))
    ax.plot([], [], marker=r"$\downarrow$", ls="none", color="#1f77b4",
            ms=10, label="limite superiore (nessun trigger)")

    # previsione: la SOLA dispersione delle ampiezze, scalata perche' il 50%
    # cada dove lo dice la misura. E' il turn-off piu' largo compatibile con
    # la distribuzione osservata, ed e' comunque un gradino.
    reali = amp[buoni(amp, pos)]
    a50 = np.median(reali)
    d50 = np.interp(0.5, eff[::-1], d[::-1])
    k = abs(a50) / d50                  # conteggi di ampiezza per conteggio di distanza
    griglia = np.linspace(0, dmax + 1, 400)
    prev = np.array([(np.abs(reali) > k * g).mean() for g in griglia])
    ax.plot(griglia, 100 * prev, lw=2.2, color="#c0392b", ls="--",
            label="previsione dalla sola dispersione\ndelle ampiezze (5-95%%: %.0f/%.0f cnt)"
                  % (np.percentile(reali, 5), np.percentile(reali, 95)))

    ax.axhline(50, color="#888", lw=1, ls=":")
    ax.annotate("50%", xy=(0.15, 52), color="#666", fontsize=9)
    ax.axvspan(0, 3.4, color="#f39c12", alpha=.13)
    ax.annotate("qui la deriva del piedistallo\nfa entrare rumore\n(purezza 55%)",
                xy=(1.7, 76), ha="center", fontsize=9, color="#8a5a00")

    ax.set_xlabel("distanza soglia-piedistallo  [conteggi, Transparent Mode]")
    ax.set_ylabel("efficienza del self-trigger  [%]")
    ax.set_title("Impulso da %.1f ns, %.1f mV: il turn-off e' troppo largo"
                 % (meta["larghezza_ns"], abs(meta["ampiezza_mv"])), fontsize=12)
    ax.set_xlim(0, dmax + 0.8)
    ax.set_ylim(-2, 105)
    ax.grid(alpha=.3)
    ax.legend(fontsize=8.5, loc="upper right")
    salva(fig, out)


def fig_popolazioni(meta, amp, pos, out):
    """Segnale e rumore si separano nel piano ampiezza-posizione."""
    p = {x["offset"]: x for x in meta["punti"]}
    fig, axes = plt.subplots(1, 2, figsize=(10.4, 4.6), sharey=True, sharex=True)
    for ax, off in zip(axes, (3, 5)):
        s = slice(p[off]["eventi_da"], p[off]["eventi_a"])
        a, q = amp[s], pos[s]
        ax.scatter(q, a, s=7, alpha=.35, color="#1f77b4", edgecolors="none")
        ax.axhspan(SOGLIA_IMPULSO, 5, color="#c0392b", alpha=.08)
        ax.axvspan(*FINESTRA_50NS, color="#2ca02c", alpha=.10)
        pur = 100 * buoni(a, q).mean()
        ax.set_title("offset %d  (distanza %.2f)   purezza %.0f%%"
                     % (off, p[off]["distanza"], pur), fontsize=11)
        ax.set_xlabel("posizione del minimo nella finestra  [ns]")
        ax.set_xlim(0, 410)
        ax.grid(alpha=.3)
    axes[0].set_ylabel("ampiezza  [conteggi]")
    axes[0].annotate("trigger di rumore:\nampiezza piccola,\nposizione casuale",
                     xy=(210, -20), fontsize=9, color="#a03020", ha="center")
    axes[0].annotate("popolazione discreta a $-$52 cnt,\n~7%: origine non spiegata",
                     xy=(255, -52), xytext=(150, -88), fontsize=8.5, color="#555",
                     arrowprops=dict(arrowstyle="->", color="#777", lw=1))
    axes[1].annotate("impulsi veri:\ntutti a 50 ns", xy=(120, -60), fontsize=9,
                     color="#1a6b1a")
    fig.suptitle("Come si riconosce un trigger di rumore", fontsize=12)
    salva(fig, out)


def liscia(x, n=101):
    """Mediana mobile: isola la deriva dalla dispersione evento per evento."""
    pad = np.pad(x, n // 2, mode="edge")
    return np.array([np.median(pad[i:i + n]) for i in range(len(x))])


def fig_deriva(meta, amp, pos, dc, chans, out):
    """La deriva e' comune ai due canali, e mangia la purezza."""
    p = {x["offset"]: x for x in meta["punti"]}
    fig, axes = plt.subplots(2, 1, figsize=(8.0, 6.4))

    ax = axes[0]
    lisci = np.column_stack([liscia(dc[:, i]) for i in range(dc.shape[1])])
    ev = np.arange(len(dc))
    for i, ch in enumerate(chans):
        ax.plot(ev, lisci[:, i] - lisci[0, i], lw=1.8,
                label="ch%d%s" % (ch, "  (collegato al generatore)" if i == 0
                                  else "  (non collegato)"))
    diff = (lisci[:, 0] - lisci[0, 0]) - (lisci[:, 1] - lisci[0, 1])
    ax.plot(ev, diff, lw=2.0, color="#c0392b",
            label="differenza  (parte non comune): %.1f cnt" % (diff.max() - diff.min()))
    ax.axhline(0, color="#999", lw=.8)
    ax.set_xlabel("evento")
    ax.set_ylabel("deriva del livello DC  [conteggi]\n(mediana mobile su 101 eventi)")
    ax.set_title("La deriva e' quasi tutta comune ai due canali: non e' il generatore",
                 fontsize=11)
    ax.legend(fontsize=8.5, ncol=2)
    ax.grid(alpha=.3)

    ax = axes[1]
    s = slice(p[3]["eventi_da"], p[3]["eventi_a"])
    a, q, level = amp[s], pos[s], liscia(dc[:, 0])[s]
    ok = buoni(a, q)
    bordi = np.percentile(level, np.linspace(0, 100, 9))
    xs, ys, es = [], [], []
    for lo, hi in zip(bordi[:-1], bordi[1:]):
        m = (level >= lo) & (level < hi)
        if m.sum() > 30:
            xs.append(level[m].mean())
            ys.append(100 * ok[m].mean())
            es.append(100 * np.sqrt(ok[m].mean() * (1 - ok[m].mean()) / m.sum()))
    ax.errorbar(xs, ys, yerr=es, fmt="o-", ms=7, capsize=3, color="#8e44ad")
    ax.set_xlabel("livello DC del canale  [conteggi]")
    ax.set_ylabel("purezza dei trigger  [%]")
    ax.set_title("A offset 3: quando il piedistallo scende verso la soglia, entra rumore",
                 fontsize=11)
    ax.grid(alpha=.3)
    salva(fig, out)


def salva(fig, nome):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, nome)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    print("grafico:", path)


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        ROOT, "measurements", "scan_1p6ns_20260924.json")
    meta = json.load(open(src))
    amp, pos, dc, chans = carica(meta)
    print("eventi letti:", len(amp))
    fig_efficienza(meta, amp, pos, "efficienza_1p6ns.png")
    fig_popolazioni(meta, amp, pos, "popolazioni_1p6ns.png")
    fig_deriva(meta, amp, pos, dc, chans, "deriva_1p6ns.png")


if __name__ == "__main__":
    main()
