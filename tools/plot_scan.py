#!/usr/bin/env python3
"""Grafico di uno scan in soglia, del self-trigger o del CFD, o dei due insieme.

    python3 tools/plot_scan.py plots/noise_scan_20261001_120000.json
    python3 tools/plot_scan.py --logy plots/scan_v812_*.json
    python3 tools/plot_scan.py plots/noise_scan_*.json plots/scan_v812_*.json

Sovrapporre i due e' il motivo per cui questo script esiste: il self-trigger e
il V812 discriminano lo stesso segnale in due modi diversi, e l'unico modo
onesto di confrontarli e' mettere entrambi i rate sulla stessa scala in
millivolt. Per il self-trigger la soglia e' in conteggi di offset, e la
conversione in millivolt dipende dalla frequenza di campionamento: si ricava
dal nome della run registrato nel JSON.
"""

import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daqio

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# mV per unita' di offset del self-trigger, corretti per la larghezza vera
# degli impulsi dei PMT (FWHM 1.80 ns, misurata a 2.5 GS/s il 2026-09-30).
# Vedi measurements/larghezza_pmt_20260930.json e CLAUDE.md.
MV_PER_OFFSET = {"2.5Gs": 3.5, "1Gs": 2.7}

# I tag che la DAQ scrive nel nome del file (Digitizer.cpp). Stanno in un
# elenco SEPARATO dalla tabella di calibrazione, e la separazione e' il punto:
# a 750 MS/s la frequenza si riconosce benissimo, ma la conversione in
# millivolt non e' misurata -- e dipende dalla frequenza del 29% fra 2.5 e
# 1 GS/s, per un meccanismo che non e' capito, quindi non si interpola.
# Tenendole insieme, un tag senza calibrazione sembrava un nome di file
# illeggibile e lo scan non si disegnava affatto.
TAG_FREQUENZA = ["5Gs", "2.5Gs", "1Gs", "750Ms"]

# Il colore distingue le SERIE, non lo strumento: due scan dello stesso
# modulo -- per esempio un canale per volta -- devono avere colori diversi, se
# no non si distinguono. A dire quale strumento sia ci pensano il marker e il
# tratto, che restano fissi per tipo.
#
# Ordine categorico fisso, mai ciclato: passa i controlli di separazione sia a
# vista normale sia col daltonismo. La separazione minima sta nella fascia che
# richiede una codifica secondaria, e c'e': marker diverso per strumento, piu'
# la legenda, che per questi grafici e' sempre presente.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
           "#e87ba4", "#008300", "#4a3aa7", "#e34948"]

MARKER = {"v1742": "o", "v812": "s"}
TRATTO = {"v1742": "-", "v812": "--"}
INCHIOSTRO = "#52514e"


def _canali(c):
    """I canali come si leggono in una legenda, non come li stampa Python.

    Con piu' di un canale si scrive OR, e non per pignoleria: il rate di uno
    scan e' sempre UNO, quello dell'OR degli ingressi abilitati, perche' sia
    l'uscita OR del V812 sia il trigger del V1742 sono un filo solo. Scritto
    "ch 0, 1" si legge come due curve che si sovrappongono perfettamente, e il
    sospetto che sia un baco e' legittimo: la colpa e' dell'etichetta.
    """
    if not c:
        return "?"
    if not isinstance(c, (list, tuple)):
        return "ch %s" % c
    if len(c) == 1:
        return "ch %s" % c[0]
    return "OR of ch %s" % ", ".join(str(x) for x in c)


MV_PER_CONTEGGIO = 1000.0 / 4096      # V1742, ingresso 1 Vpp a 12 bit


def per_canale(d, tipo, k_mv_offset):
    """Quante volte OGNI canale ha superato la soglia, punto per punto.

    Il rate che lo scan misura e' uno solo, quello dell'OR: sia l'uscita OR
    del V812 sia il trigger del V1742 sono un filo solo e non dicono quale
    ingresso abbia sparato. Pero' negli eventi registrati ci sono tutte le
    forme d'onda, quindi il conto per canale si rifa' a posteriori.

    Attenzione a cosa si sta misurando: la soglia viene applicata qui
    all'ampiezza RICOSTRUITA in Output Mode, mentre in hardware il
    discriminatore guarda altro -- il segnale diretto per il V812, la copia
    attenuata in Transparent Mode per il self-trigger. E' una ricostruzione di
    cosa ha fatto il discriminatore, non il suo conteggio.

    Vale inoltre solo per i canali che partecipano al trigger: se un canale
    non e' nell'OR, lo si vede solo quando ha sparato qualcun altro, e il
    conteggio sarebbe quello delle coincidenze, non delle sue soglie.

    Ritorna {canale: (soglie_mV, rate_Hz)}.
    """
    dati = os.path.join(ROOT, "data")
    fuori = {}

    for p in d.get("punti", []):
        if tipo == "v812":
            nome, durata = p.get("file"), p.get("durata_s")
            soglia_mv, prima, dopo = p.get("soglia_mv"), None, None
        else:
            nome, durata = d.get("file"), d.get("secondi_per_punto")
            soglia_mv = p.get("distanza", 0) * k_mv_offset
            prima, dopo = p.get("eventi_da"), p.get("eventi_a")
        if not nome or not durata:
            continue

        percorso = os.path.join(dati, nome)
        if not os.path.exists(percorso):
            percorso += ".gz"
        if not os.path.exists(percorso):
            print("  (salto %s: file non trovato)" % nome, file=sys.stderr)
            continue

        try:
            # Per il V1742 i punti stanno tutti nello stesso file: si legge
            # fino alla fine del punto e si taglia. Leggere un intervallo
            # qualunque richiederebbe di toccare daqio, che e' usato da tutto
            # il resto: non vale il rischio per un conteggio.
            hdr, dd = daqio.load(percorso,
                                 max_events=(dopo if dopo else None),
                                 live=not percorso.endswith(".gz"))
        except Exception as e:
            print("  (salto %s: %s)" % (nome, str(e)[:60]), file=sys.stderr)
            continue
        if prima:
            dd = dd[prima:]
        if dd.shape[0] == 0:
            continue

        _, _, amp, _ = daqio.baseline_amplitude(dd)
        for i, ch in enumerate(hdr["ChannelList"]):
            n = int((np.abs(amp[:, i]) * MV_PER_CONTEGGIO > soglia_mv).sum())
            fuori.setdefault(int(ch), ([], []))
            fuori[int(ch)][0].append(soglia_mv)
            fuori[int(ch)][1].append(n / float(durata))

    return {c: (np.array(x), np.array(y)) for c, (x, y) in fuori.items()}


def frequenza(nome_file):
    """Frequenza di campionamento dedotta dal nome della run."""
    for tag in TAG_FREQUENZA:
        if "_%s_" % tag in nome_file:
            return tag
    return None


def leggibile(tag):
    """Il tag come si scrive su un asse: '750Ms' -> '750 MS/s'."""
    if not tag:
        return "?"
    return tag.replace("Gs", " GS/s").replace("Ms", " MS/s")


def leggi(path, mv_per_offset_forzato):
    """Normalizza i due formati in (tipo, soglie, rate, limite, nota, dati, unita).

    L'ultimo elemento e' l'unita' dell'asse x, "mV" oppure "offset": senza una
    calibrazione misurata alla frequenza della run il grafico si fa comunque,
    ma in conteggi di offset, e chi legge deve saperlo.
    """
    d = json.load(open(path))
    punti = d.get("punti", [])
    if not punti:
        raise SystemExit("%s non contiene punti." % path)

    if d.get("tipo") == "v812":
        x = np.array([p["soglia_mv"] for p in punti], dtype=float)
        y = np.array([p["rate"] for p in punti], dtype=float)
        lim = np.array([p["eventi"] == 0 for p in punti])
        nota = "V812 CFD   %s" % _canali(d.get("canali"))
        return "v812", x, y, lim, nota, d, "mV"

    # formato di noise_scan.py: soglia in conteggi di offset
    tag = frequenza(d.get("file", ""))
    k = mv_per_offset_forzato or MV_PER_OFFSET.get(tag)

    # Il valore assoluto, non il numero firmato: "distanza" e' baseline meno
    # soglia, quindi cambia segno col fronte di discriminazione -- positiva
    # con gli impulsi negativi dei PMT, NEGATIVA con un SiPM positivo, dove la
    # soglia sta sopra il piedistallo. Il segno dice solo da che parte del
    # piedistallo si discrimina, che e' nel titolo della run; quello che si
    # grafica e di cui si confrontano gli scan e' la distanza.
    dist = np.abs(np.array([p["distanza"] for p in punti], dtype=float))
    y = np.array([max(p["rate"], 0.0) for p in punti], dtype=float)
    lim = np.array([p.get("conteggi", 1) == 0 for p in punti])

    if k is None:
        # Senza calibrazione si disegna comunque, in conteggi di offset: e' la
        # grandezza che lo scan ha davvero variato. Inventare un fattore --
        # anche solo prendendo quello della frequenza vicina -- vorrebbe dire
        # scrivere "mV" su un asse che non lo e'.
        nota = "V1742 self-trigger   %s   (offset counts: mV NOT CALIBRATED at %s)" % (
            _canali(d.get("canali")), leggibile(tag))
        return "v1742", dist, y, lim, nota, d, "offset"

    nota = "V1742 self-trigger   %s   (%.1f mV/offset at %s)" % (
        _canali(d.get("canali")), k, leggibile(tag))
    return "v1742", dist * k, y, lim, nota, d, "mV"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json", nargs="+", help="uno o piu' file di scan")
    ap.add_argument("--mv-per-offset", type=float, default=None,
                    help="forza la conversione offset -> mV del self-trigger")
    ap.add_argument("--per-channel", action="store_true",
                    help="una curva per CANALE invece del rate del trigger: "
                         "quante volte ogni canale ha superato la soglia, "
                         "ricontato sulle forme d'onda registrate")
    ap.add_argument("--channels", default=None,
                    help="con --per-channel, quali canali disegnare (es. 8,9). "
                         "Di default tutti quelli presenti nel file, compresi "
                         "quelli scollegati, che restano piatti a zero")
    ap.add_argument("--logy", action="store_true",
                    help="asse dei rate logaritmico. Di default e' lineare: il "
                         "logaritmo fa vedere bene le code basse ma schiaccia la "
                         "parte alta, dove di solito sta la soglia che interessa")
    ap.add_argument("-o", "--out", default=None, help="file PNG di uscita")
    args = ap.parse_args()

    fig, ax = plt.subplots(figsize=(7.4, 4.8))
    visti = []

    if len(args.json) > len(PALETTE):
        raise SystemExit(
            "%d scan in un grafico solo: i colori distinguibili sono %d.\n"
            "Oltre, le curve non si distinguono piu' e il grafico smette di dire "
            "qualcosa. Falli in due figure." % (len(args.json), len(PALETTE)))

    # Si leggono tutti prima di disegnare: due scan della stessa cosa darebbero
    # la stessa etichetta, e una legenda con due voci identiche non distingue
    # niente. Quando succede si aggiunge l'ora dello scan.
    serie = [leggi(path, args.mv_per_offset) for path in args.json]

    # Un asse, una unita'. Mescolare uno scan in millivolt con uno in conteggi
    # di offset significherebbe disegnare due grandezze diverse sulla stessa
    # ascissa: le curve starebbero una accanto all'altra e il confronto, che e'
    # il motivo per cui questo script sovrappone gli scan, sarebbe falso.
    unita = sorted({x[6] for x in serie})
    if len(unita) > 1:
        raise SystemExit(
            "Questi scan non stanno sullo stesso asse: %s.\n"
            "Succede quando uno e' a una frequenza con la calibrazione misurata e "
            "un altro no (per esempio 750 MS/s).\n"
            "Con --mv-per-offset <valore> si forza la conversione per tutti, "
            "sapendo che a quella frequenza e' un'assunzione." % ", ".join(unita))
    unita = unita[0]

    note = [s[4] for s in serie]
    for i, s in enumerate(serie):
        if note.count(s[4]) > 1:
            quando = (s[5].get("quando") or "")[5:16].replace("-", "/")
            serie[i] = s[:4] + (s[4] + ("   [%s]" % quando if quando else "   [%d]" % (i + 1)),) + s[5:]

    if args.per_channel:
        # Una curva per canale, ricontata sui dati: il rate del trigger non
        # sa dire quale ingresso abbia sparato, le forme d'onda si'.
        n = 0
        for tipo, _, _, _, nota, d, _u in serie:
            # Qui il fattore serve per forza: il conteggio per canale confronta
            # la soglia con l'ampiezza RICOSTRUITA, che e' in millivolt, quindi
            # la soglia va portata in millivolt e non c'e' un asse alternativo
            # in conteggi di offset. Prima c'era un default nascosto di 3.5
            # mV/offset: a 750 MS/s avrebbe applicato la calibrazione dei
            # 2.5 GS/s senza dirlo, cioe' un numero sbagliato scritto "mV".
            k = args.mv_per_offset or (MV_PER_OFFSET.get(frequenza(d.get("file", "")))
                                       if tipo == "v1742" else 1.0)
            if k is None:
                raise SystemExit(
                    "Il conteggio per canale confronta la soglia con l'ampiezza "
                    "ricostruita in millivolt, e a %s la conversione "
                    "offset -> mV non e' misurata.\n"
                    "Il grafico del rate del trigger si fa comunque (in conteggi di "
                    "offset): togli l'opzione per canale.\n"
                    "Per forzarla: --mv-per-offset <valore>, sapendo che a quella "
                    "frequenza e' un'assunzione."
                    % leggibile(frequenza(d.get("file", ""))))
            canali = per_canale(d, tipo, k)
            if not canali:
                raise SystemExit("Non sono riuscito a rileggere nessun dato: "
                                 "i file delle run ci sono ancora in data/?")
            voluti = None
            if args.channels:
                voluti = {int(x) for x in args.channels.replace(",", " ").split()}
            for ch in sorted(c for c in canali if voluti is None or c in voluti):
                if n >= len(PALETTE):
                    raise SystemExit("Troppe curve per i colori disponibili: "
                                     "scegli meno canali o meno scan.")
                x, y = canali[ch]
                ordine = np.argsort(x)
                etichetta = "ch %d" % ch
                if len(serie) > 1:
                    etichetta += "   (%s)" % nota.split("  ")[0]
                ax.plot(x[ordine], y[ordine], marker=MARKER[tipo], ls=TRATTO[tipo],
                        ms=8, lw=2.0, color=PALETTE[n], mec="white", mew=1.2,
                        label=etichetta)
                n += 1
            visti.append(tipo)
        serie = []

    for n, (tipo, x, y, lim, nota, d, _u) in enumerate(serie):
        col, mk, ls = PALETTE[n], MARKER[tipo], TRATTO[tipo]
        visti.append(tipo)

        # I punti stanno nel JSON nell'ordine in cui sono stati misurati, che
        # non e' detto sia crescente in soglia: gli scan a volte ripetono un
        # punto in fondo per verificare la riproducibilita'. Senza riordinare,
        # la spezzata torna indietro e sembra un secondo ramo della curva.
        ordine = np.argsort(x)
        x, y, lim = x[ordine], y[ordine], lim[ordine]

        vis = ~lim
        if vis.any():
            # Il valore si schiaccia a un minimo positivo solo in scala
            # logaritmica, dove uno zero non sarebbe rappresentabile; in
            # lineare si disegna il numero misurato.
            yv = np.maximum(y[vis], 1e-3) if args.logy else y[vis]
            ax.plot(x[vis], yv, marker=mk, ls=ls, ms=8,
                    lw=2.0, color=col, mec="white", mew=1.2, label=nota)
        if lim.any():
            # Zero eventi non e' rate zero: su scala logaritmica sarebbe
            # invisibile, e spacciarlo per una misura sarebbe peggio.
            soffitto = 1.0 / d.get("secondi_per_punto", 1.0)
            ax.plot(x[lim], [soffitto] * lim.sum(), marker="v", ls="none",
                    ms=9, color=col, mec="white", mew=1.2, alpha=.8,
                    label="upper limit, no events (%s)" % nota.split("  ")[0])

    if args.logy:
        ax.set_yscale("log")
    else:
        # In lineare il rate parte da zero: un asse che non lo include
        # falserebbe la lettura di quanto una curva e' scesa.
        ax.set_ylim(bottom=0)

    if unita == "mV":
        ax.set_xlabel("threshold  [mV at the detector input]")
    else:
        # Si dice "from baseline" perche' e' una distanza, non una soglia
        # assoluta: il piedistallo sta scritto nel JSON di ogni scan.
        ax.set_xlabel("threshold distance from baseline  [offset counts]")
    if args.per_channel:
        ax.set_ylabel("rate above threshold  [Hz]")
        ax.set_title("Threshold scan — per channel", fontsize=11)
    else:
        ax.set_ylabel("trigger rate  [Hz]")
        ax.set_title("Threshold scan", fontsize=11)
    ax.grid(alpha=.3, which="both" if args.logy else "major")
    ax.legend(fontsize=8.5)

    # La nota va FUORI dagli assi. Dentro non esiste un posto sicuro: avevo
    # provato in basso a sinistra (ci finiscono i limiti superiori) e poi a
    # meta' altezza a destra, ragionando che il rate cala sempre con la soglia
    # -- vero, ma in scala logaritmica la curva crolla proprio li' e il
    # riquadro le finiva sopra. Sotto il grafico non puo' collidere con niente,
    # qualunque siano i dati e la scala.
    sotto = ("v1742" in visti and "v812" in visti) and not args.per_channel
    if sotto:
        fig.text(0.5, 0.012,
                 "Both discriminators see the same signal. "
                 "A gap at equal threshold is efficiency, not calibration.",
                 ha="center", fontsize=8, color=INCHIOSTRO)

    out = args.out or os.path.join(ROOT, "plots",
                                   "scan_%s.png" % time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.tight_layout(rect=(0, 0.04, 1, 1) if sotto else None)
    fig.savefig(out, dpi=130)
    plt.close(fig)
    print("grafico: %s" % out)


if __name__ == "__main__":
    main()
