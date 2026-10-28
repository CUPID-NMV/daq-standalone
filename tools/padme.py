#!/usr/bin/env python3
"""Calibrazione PADME del piedistallo per cella del DRS4, da una run a
TRIGGER SOFTWARE, e verifica di quanto riduce il rumore.

    python3 tools/padme.py data/LNGS/test__54-0V_0036_750Ms_50PT.h5.gz --canali 20 21

Il metodo e' quello del test beam (Testbeam2026_WC_unified, wclib.py): ogni
cella del DRS4 ha un suo piedistallo, e il campione k di un evento sta nella
cella (k + SIC) mod 1024, con SIC la Start Index Cell del gruppo. La tabella
e' la MEDIANA, per cella, dei campioni caduti in quella cella; la correzione
toglie a ogni campione il valore della sua cella:

    S_cor(k) = S(k) - E[(k + SIC) mod 1024]

Si applica SOPRA la correzione della CAEN, se era accesa nella run (al test
beam lo era): quella toglie le differenze fra le celle calibrate in
fabbrica, questa il residuo misurato sulla scheda com'e' oggi. Una tabella
vale quindi per la stessa frequenza, lo stesso DCOffset e lo stesso stato
della correzione CAEN delle run a cui la si applica.

Perche' una run a trigger software: le finestre sono cieche, quindi sui PMT
quasi tutto e' piedistallo. Sui SiPM ci sono gli impulsi al buio (~2 MHz,
qualche impulso per finestra): la mediana li regge, ma la tabella dei SiPM
va presa con piu' cautela di quella dei PMT.

Calibrazione e verifica su META' DISGIUNTE degli eventi (pari e dispari):
misurare il guadagno sugli stessi eventi da cui si e' presa la tabella
premierebbe anche il rumore che la tabella ha imparato a memoria. La
differenza fra le due tabelle, quella dei pari e quella dei dispari, e'
l'errore statistico della tabella.

Una cautela misurata il 2026-10-23: col trigger software a 750 MS/s la SIC
prende solo ~40 posizioni. Ogni cella si vede allora in pochi indici di
lettura, e una struttura legata all'INDICE (la posizione nella traccia) si
confonde con una legata alla CELLA. Per questo lo strumento calcola anche la
tabella per indice di lettura e prova le due correzioni separate e in
sequenza: se la tabella per indice da sola rende quanto quella per cella, i
dati non distinguono le due cose.
"""

import argparse
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daqio        # noqa: E402
import efficienza   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
N_CELLE = 1024
CANALI_PER_GRUPPO = 8
MV_PER_CONTEGGIO = efficienza.MV_PER_CONTEGGIO
EVENTI_DI_SERIE = 20000


def leggi(path, canali, n_max, serve_sic=True):
    """Forme d'onda (int16, eventi x canali x campioni), SIC (eventi x 4) e
    header. Si legge con h5py invece che con daqio.load perche' serve
    /events/start_index_cell, e perche' 20000 eventi in float64 sono un
    giga di memoria per niente. Con serve_sic=False un file senza SIC
    (formato fino al 2) si legge lo stesso, e la SIC torna None."""
    f, tmp = daqio.open_file(path)
    try:
        hdr = daqio.read_header(f)
        if "/events/start_index_cell" not in f and serve_sic:
            sys.exit("Errore: il file non ha /events/start_index_cell (formato %s): "
                     "senza SIC la correzione per cella non si puo' fare."
                     % hdr.get("FormatVersion"))
        tutti = [int(c) for c in hdr["ChannelList"]]
        voluti = canali or tutti
        mancanti = [c for c in voluti if c not in tutti]
        if mancanti:
            sys.exit("Errore: canali non presenti nel file: %s (ci sono %s)" % (mancanti, tutti))
        ns = int(hdr["SamplesPerChannel"])
        ds = f["/events/waveforms"]
        n = min(ds.shape[0], n_max) if n_max else ds.shape[0]
        dati = np.stack([np.asarray(ds[:n, tutti.index(c) * ns:(tutti.index(c) + 1) * ns],
                                    dtype=np.int16) for c in voluti], axis=1)
        sic = (np.asarray(f["/events/start_index_cell"][:n], dtype=np.int64)
               if "/events/start_index_cell" in f else None)
    finally:
        f.close()
        daqio._cleanup(tmp)
    return hdr, voluti, dati, sic


def correzione_caen(path):
    """Stato della correzione CAEN nella run, dal log: nell'header non c'e'."""
    log = re.sub(r"\.h5(\.gz)?$", ".log", path)
    try:
        testo = open(log, errors="replace").read()
    except OSError:
        return None
    if "DRS4 corrections ENABLED" in testo:
        return True
    if "DRS4 corrections DISABLED" in testo or "DRS4 corrections disabled" in testo:
        return False
    return None


def mediana_per_gruppo(indici, valori, n_gruppi):
    """Mediana di `valori` per ogni valore di `indici` (0..n_gruppi-1), con un
    solo ordinamento invece di un ciclo sui campioni."""
    i = indici.ravel()
    v = valori.ravel()
    ordine = np.lexsort((v, i))
    i, v = i[ordine], v[ordine]
    conta = np.bincount(i, minlength=n_gruppi)
    inizio = np.concatenate(([0], np.cumsum(conta)[:-1]))
    med = np.full(n_gruppi, np.nan)
    pieni = conta > 0
    # Mediana di un gruppo gia' ordinato: media dei due elementi centrali.
    lo = inizio[pieni] + (conta[pieni] - 1) // 2
    hi = inizio[pieni] + conta[pieni] // 2
    med[pieni] = 0.5 * (v[lo].astype(float) + v[hi].astype(float))
    return med, conta


def sigma_robusta(x, taglio=3.5, giri=5):
    """Deviazione standard con esclusione iterativa a 3.5 sigma, come in
    efficienza.py: sui SiPM gli impulsi al buio gonfierebbero quella
    semplice, e la MAD su campioni interi e' a sua volta intera."""
    x = np.asarray(x, float).ravel()
    m, s = np.mean(x), np.std(x)
    for _ in range(giri):
        dentro = np.abs(x - m) < taglio * s
        if dentro.sum() < 10:
            break
        m, s = np.mean(x[dentro]), np.std(x[dentro])
    return float(s)


def rumore_per_evento(y):
    """Il numero che conta per la carica: la dispersione dei campioni dentro
    ogni traccia, attorno al piedistallo di QUELLA traccia. Una struttura
    lenta comune a tutti gli eventi resta dentro; uno spostamento di tutta la
    traccia no, e' il piedistallo per evento a toglierlo."""
    return sigma_robusta(y - np.median(y, axis=1, keepdims=True))


def analizza_canale(y, sic):
    """y: eventi x campioni (int16), sic: SIC del gruppo per evento."""
    n, ns = y.shape
    k = np.arange(ns)
    cella = (k[None, :] + sic[:, None]) % N_CELLE
    indice = np.broadcast_to(k[None, :], (n, ns))
    pari, dispari = slice(0, None, 2), slice(1, None, 2)

    # Tabelle dalla meta' pari, controprova dalla meta' dispari.
    t_cella, conta = mediana_per_gruppo(cella[pari], y[pari], N_CELLE)
    t_cella_b, _ = mediana_per_gruppo(cella[dispari], y[dispari], N_CELLE)
    t_indice, _ = mediana_per_gruppo(indice[pari], y[pari], ns)

    # Le tabelle si tengono a media nulla: il livello assoluto lo toglie il
    # piedistallo per evento, e cosi' la tabella dice solo di quanto ogni
    # cella si scosta dalle altre.
    rif = np.nanmedian(t_cella)
    t_cella -= rif
    t_cella_b -= np.nanmedian(t_cella_b)
    t_indice -= np.nanmedian(t_indice)
    t_cella_nan0 = np.nan_to_num(t_cella)

    # Struttura per indice che resta dopo la correzione per cella, e
    # viceversa: dicono quanto le due cose si confondono.
    yp = y[pari].astype(float)
    res_idx, _ = mediana_per_gruppo(indice[pari], yp - t_cella_nan0[cella[pari]], ns)
    res_idx -= np.nanmedian(res_idx)

    yb = y[dispari].astype(float)
    cb = cella[dispari]
    varianti = {
        "grezzo": yb,
        "cella": yb - t_cella_nan0[cb],
        "indice": yb - t_indice[None, :],
        "cella+indice": yb - t_cella_nan0[cb] - np.nan_to_num(res_idx)[None, :],
    }
    rumore = {nome: rumore_per_evento(v) for nome, v in varianti.items()}
    # Dispersione della media della traccia fra un evento e l'altro: e'
    # quanto sbaglia un piedistallo preso su una finestra corta.
    pieni = conta > 0
    return {
        "eventi": int(n),
        "campioni": int(ns),
        "posizioni_sic": int(len(np.unique(sic))),
        "campioni_per_cella_min": int(conta[pieni].min()) if pieni.any() else 0,
        "celle_viste": int(pieni.sum()),
        "rumore": rumore,
        "dispersione_fra_celle": float(np.nanstd(t_cella)),
        "errore_tabella": float(np.nanstd(t_cella - t_cella_b) / np.sqrt(2)),
        "dispersione_per_indice": float(np.nanstd(t_indice)),
        "residuo_per_indice_dopo_cella": float(np.nanstd(res_idx)),
        "piedistallo": float(rif),
        "tabella_cella": t_cella,
        "tabella_cella_dispari": t_cella_b,
        "tabella_indice": t_indice,
        "esempio": {"grezzo": varianti["grezzo"][0], "cella": varianti["cella"][0]},
    }


def grafico(risultati, titolo, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    canali = list(risultati)
    fig, assi = plt.subplots(len(canali), 3, figsize=(16, 3.4 * len(canali)), squeeze=False)
    for riga, ch in zip(assi, canali):
        r = risultati[ch]
        a = riga[0]
        a.plot(r["tabella_cella"], lw=0.7, label="even events (calibration)")
        a.plot(r["tabella_cella_dispari"], lw=0.7, alpha=0.6, label="odd events (check)")
        a.set_title("ch%d  pedestal per DRS4 cell  (spread %.2f, table error %.2f counts)"
                    % (ch, r["dispersione_fra_celle"], r["errore_tabella"]), fontsize=9)
        a.set_xlabel("DRS4 cell")
        a.set_ylabel("offset [counts]")
        a.legend(fontsize=7)
        a.grid(alpha=0.3)

        a = riga[1]
        nomi = ["grezzo", "indice", "cella", "cella+indice"]
        etichette = {"grezzo": "CAEN only", "indice": "+ per read index",
                     "cella": "+ PADME (per cell)", "cella+indice": "+ PADME + read index"}
        v = [r["rumore"][n] for n in nomi]
        b = a.bar(range(len(nomi)), v, color=["#888", "#c9a227", "#2a7ab9", "#1b4f72"])
        for x, val in zip(range(len(nomi)), v):
            a.text(x, val, "%.2f\n(%.2f mV)" % (val, val * MV_PER_CONTEGGIO),
                   ha="center", va="bottom", fontsize=8)
        a.set_xticks(range(len(nomi)))
        a.set_xticklabels([etichette[n] for n in nomi], fontsize=8)
        a.set_ylabel("noise within the trace [counts]")
        a.set_ylim(0, max(v) * 1.3)
        a.set_title("noise on the odd events (not used for the table)", fontsize=9)
        a.grid(alpha=0.3, axis="y")

        a = riga[2]
        g, c = r["esempio"]["grezzo"], r["esempio"]["cella"]
        a.plot(g - np.median(g), lw=0.6, color="#888", label="CAEN only")
        a.plot(c - np.median(c), lw=0.6, color="#2a7ab9", label="+ PADME")
        a.set_title("one trace, pedestal subtracted", fontsize=9)
        a.set_xlabel("sample")
        a.set_ylabel("[counts]")
        a.legend(fontsize=7)
        a.grid(alpha=0.3)
    fig.suptitle(titolo)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--canali", type=int, nargs="+", help="canali (di serie tutti quelli del file)")
    ap.add_argument("-n", "--max-eventi", type=int, default=EVENTI_DI_SERIE,
                    help="eventi da leggere, divisi a meta' fra calibrazione e verifica "
                         "(di serie %d; 0 = tutti)" % EVENTI_DI_SERIE)
    ap.add_argument("--forza", action="store_true",
                    help="anche un file che non e' a solo trigger software")
    ap.add_argument("--json", default=None, help="di serie measurements/padme_<run>.json")
    ap.add_argument("--png", default=None, help="di serie accanto al JSON")
    a = ap.parse_args()

    hdr, canali, dati, sic = leggi(a.file, a.canali, a.max_eventi or None)
    modo = str(hdr.get("TriggerMode", "?"))
    if modo != "Software":
        msg = ("il file dichiara TriggerMode = %r: le finestre non sono cieche, e gli impulsi "
               "entrano nella tabella." % modo)
        if not a.forza:
            sys.exit("Errore: " + msg + " (--forza per farlo lo stesso)")
        print("ATTENZIONE: " + msg)

    run = efficienza.nome_run(a.file)
    caen = correzione_caen(a.file)
    print("%s: %d eventi (meta' calibrazione, meta' verifica), %d campioni, %s, correzione CAEN %s"
          % (run, dati.shape[0], dati.shape[2], hdr.get("SamplingRate", "?"),
             {True: "ACCESA", False: "spenta", None: "sconosciuta"}[caen]))

    risultati = {}
    print("%4s %9s %12s %10s %10s %10s %12s %12s %12s" % (
        "ch", "pos. SIC", "fra celle", "err.tab.", "grezzo", "PADME", "per indice",
        "PADME+ind.", "guadagno"))
    for j, ch in enumerate(canali):
        g = ch // CANALI_PER_GRUPPO
        s = sic[:, g]
        if np.any(s > N_CELLE - 1):
            print("ch%d: SIC fuori da 0-1023 (gruppo %d assente?), salto" % (ch, g))
            continue
        r = analizza_canale(dati[:, j, :], s)
        r["gruppo"] = g
        risultati[ch] = r
        ru = r["rumore"]
        print("%4d %9d %12.2f %10.2f %10.2f %10.2f %12.2f %12.2f %11.2fx" % (
            ch, r["posizioni_sic"], r["dispersione_fra_celle"], r["errore_tabella"],
            ru["grezzo"], ru["cella"], ru["indice"], ru["cella+indice"],
            ru["grezzo"] / ru["cella"] if ru["cella"] > 0 else float("nan")))
    print("Valori in conteggi ADC (1 conteggio = %.3f mV). 'fra celle' e' la dispersione della "
          "tabella; 'err.tab.' quella dovuta alla statistica, dal confronto pari/dispari.\n"
          "Rumore = dispersione dei campioni dentro la traccia attorno al suo piedistallo, "
          "misurata sugli eventi DISPARI, non usati per la tabella." % MV_PER_CONTEGGIO)

    os.makedirs(os.path.join(ROOT, "measurements"), exist_ok=True)
    pj = a.json or os.path.join(ROOT, "measurements", "padme_%s.json" % run)
    pp = a.png or os.path.splitext(pj)[0] + ".png"

    def serial(v):
        if isinstance(v, np.ndarray):
            return [None if not np.isfinite(x) else round(float(x), 3) for x in v.astype(float)]
        if isinstance(v, dict):
            return {k: serial(x) for k, x in v.items()}
        return v
    with open(pj, "w") as f:
        json.dump({"file": os.path.abspath(a.file), "run": run, "trigger_mode": modo,
                   "sampling_rate": hdr.get("SamplingRate"), "correzione_caen": caen,
                   "board": hdr.get("Board"),
                   # L'header lo da' come uint32 di numpy, che json non sa scrivere.
                   "board_serial": (int(hdr["BoardSerial"]) if "BoardSerial" in hdr else None),
                   "mv_per_conteggio": MV_PER_CONTEGGIO,
                   "nota": "tabella_cella[c] = scostamento della cella c dalla mediana delle "
                           "celle, in conteggi; S_cor(k) = S(k) - tabella_cella[(k+SIC) mod 1024], "
                           "SIC del gruppo del canale. Tabella dagli eventi pari.",
                   "canali": {str(ch): serial({k: v for k, v in r.items()})
                              for ch, r in risultati.items()}}, f, indent=1)
    grafico(risultati, "%s — PADME per-cell pedestal correction (software trigger)" % run, pp)
    print("scritti %s e %s" % (pj, pp))


if __name__ == "__main__":
    main()
