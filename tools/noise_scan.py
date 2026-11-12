#!/usr/bin/env python3
"""
Pavimento di rumore del self-trigger: rate di trigger in funzione dell'offset,
con il segnale spento.

Senza segnale gli unici trigger vengono dal rumore, quindi la misura non
dipende ne' dallo spettro delle ampiezze ne' dalla stabilita' della sorgente:
e' la curva pulita che lo scan in efficienza non riusciva a dare.

Il rate atteso e' la coda gaussiana del rumore campionato dall'ADC in
Transparent Mode:

    R(offset) = f_ADC * n_canali * 0.5 * erfc( offset / (sigma * sqrt2) )

con sigma misurato dal dump in Transparent Mode. Confrontare misura e
previsione verifica il modello di rumore, e quindi tutte le soglie espresse
in unita' di sigma.

Serve una run gia' avviata con SelfTrigger = true E IL SEGNALE SPENTO
(alta tensione dei PMT giu', o guadagno al minimo).

    python3 tools/noise_scan.py --offsets 5 4.5 4 3.5 3 2.5 --sigma 0.72

Con --canale lo scan muove UN solo canale, col segnale acceso: gli altri
canali del trigger stanno alla soglia massima, cosi' il rate e' quello del
canale scelto, e a fine scan (anche se interrotto) tornano tutti agli offset
di partenza. Serve quando sulla scheda ci sono rivelatori diversi -- SiPM e
PMT -- che con lo stesso offset non hanno niente in comune.

    python3 tools/noise_scan.py --canale 20 --offsets 400 200 100 50 --seconds 60

Con --scheda lavora sui file di UNA scheda del controllore a schede
(live-status-<scheda>.json, live-threshold-<scheda>.txt). Per un VX2730B i
valori sono la soglia in conteggi ADC dalla base (la scheda la tiene relativa
alla base da se'), non offset di un DAC; a fine scan, anche interrotto, le
soglie tornano quelle di partenza. Lo lancia tools/scan_scheda.sh.

    python3 tools/noise_scan.py --scheda vx2730 --canale 0 --offsets 100 60 40 30 20
"""

import argparse
import glob
import json
import math
import os
import re
import signal
import subprocess
import sys
import time

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daqio import count_events, load as carica_run

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F_ADC = 30e6          # campionamento dell'ADC in Transparent Mode, ~30 MHz

# Soglia dei canali fermi nello scan di un canale, per una scheda a soglia
# relativa (VX2730B): il massimo che la scheda accetta, 16383 conteggi dalla
# base, cioe' oltre tutta la dinamica: quei canali non possono scattare.
PARCHEGGIO_RELATIVO = 16383

# La scheda dello scan, se ce n'e' una (--scheda): decide i nomi dei file di
# stato e di comando. Globale perche' la usano tutte le funzioni di I/O.
SCHEDA = None


def _nome(base):
    """live-status.json -> live-status-<scheda>.json, se c'e' una scheda."""
    if not SCHEDA:
        return base
    radice, est = os.path.splitext(base)
    return "%s-%s%s" % (radice, SCHEDA, est)


def status(data_dir):
    with open(os.path.join(data_dir, _nome("live-status.json"))) as f:
        return json.load(f)


def relativa(st):
    """True se la soglia e' relativa alla base (VX2730B): la base non si
    conosce, e la distanza dalla base e' la soglia stessa."""
    return st.get("threshold_mode") == "relative"


def n_events(path):
    return count_events(path, live=True)


def set_offset(data_dir, channels, offset, timeout=15):
    """Imposta l'offset e restituisce cio' che la DAQ ha davvero scritto.

    L'offset richiesto non e' la soglia: il registro e' a 12 bit, quindi
    offset diversi possono finire sullo stesso valore intero. Serve la
    distanza vera fra baseline e soglia, che e' la grandezza fisica.
    """
    return set_offsets(data_dir, {ch: offset for ch in channels}, timeout)


def set_offsets(data_dir, offsets, timeout=15):
    """Come set_offset, con un offset per canale: {canale: offset}."""
    path = os.path.join(data_dir, _nome("live-threshold.txt"))
    # La DAQ si accorge del comando dal tempo di modifica del file, che ha la
    # risoluzione del secondo: una seconda scrittura nello stesso secondo non
    # la vede, e il passo aspetterebbe un offset che non arriva mai. Succede
    # fra il ripristino di un canale e il primo punto del successivo.
    try:
        while int(time.time()) <= int(os.path.getmtime(path)):
            time.sleep(0.1)
    except OSError:
        pass
    with open(path, "w") as f:
        # %g: 20.0 si scrive "20". Il VX2730B vuole una soglia intera e
        # "20.0" potrebbe non prenderlo per tale; il V1742 legge tutti e due.
        for ch, off in offsets.items():
            f.write(f"{ch} {off:g}\n")
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = status(data_dir)
        applied = {int(c["ch"]): c["offset"] for c in st["channels"]}
        if all(abs(applied.get(ch, -1) - off) < 1e-6 for ch, off in offsets.items()):
            return {int(c["ch"]): (c["baseline"], int(c["threshold"]))
                    for c in st["channels"]}
        time.sleep(0.5)
    sys.exit(perche_non_applica(data_dir, offsets, timeout))


def daq_viva():
    """True se gira un processo della DAQ. pgrep solo per leggere."""
    try:
        return subprocess.run(["pgrep", "-f", "main/DAQ-WC"], capture_output=True).returncode == 0
    except OSError:
        return True          # non si sa: meglio non affermare il contrario


def perche_non_applica(data_dir, offset, timeout):
    """Il motivo per cui la soglia nuova non arriva, invece di un generico
    "non applicata". Il caso piu' comune non e' un guasto: la run e' FINITA,
    di solito perche' ha raggiunto NEvents. Successo il 2026-10-26: NEvents =
    100000 col self-trigger saturo a ~940 Hz e' meno di due minuti, lo scan
    si e' fermato al sesto passo dicendo che la DAQ "non aveva applicato"
    l'offset, mentre la DAQ era chiusa da venti secondi."""
    if isinstance(offset, dict):
        offset = ", ".join(f"ch{c} {o:g}" for c, o in offset.items())
    else:
        offset = f"{offset:g}"
    msg = f"La DAQ non ha applicato l'offset {offset} entro {timeout} s."
    if not daq_viva():
        log = ultimo_log(data_dir)
        fine = ""
        if log:
            testo = open(log, errors="replace").read()
            m = re.search(r"Total events recorded:\s*(\d+)", testo)
            n = re.search(r"(?m)^NEvents\s*=\s*(\d+)", testo)
            if m and n and int(m.group(1)) >= int(n.group(1)):
                fine = (f" Ha raggiunto NEvents = {n.group(1)}: per uno scan del "
                        "self-trigger va alzato (la DAQ satura a ~940 Hz).")
            elif m:
                fine = f" Si e' chiusa dopo {m.group(1)} eventi (vedi {os.path.basename(log)})."
        return msg + " La DAQ NON E' PIU' IN ESECUZIONE: la run e' finita." + fine
    try:
        # Il VX2730B riscrive lo stato solo quando cambia una soglia, non di
        # continuo come il V1742: la sua eta' non dice se la DAQ e' viva. Il
        # motivo piu' probabile li' e' un valore che la scheda ha rifiutato,
        # e lo dice il log della run.
        if relativa(status(data_dir)):
            return msg + (" La scheda puo' averlo rifiutato (fuori scala): guarda il log "
                          "della run, riga \"Live threshold\".")
        eta = time.time() - os.path.getmtime(os.path.join(data_dir, _nome("live-status.json")))
        if eta > 10:
            return msg + (f" La DAQ gira, ma live-status.json non si aggiorna da {eta:.0f} s: "
                          "e' bloccata, o scrive lo stato in un'altra cartella (OutputDir).")
    except OSError:
        pass
    return msg


def ultimo_log(data_dir):
    logs = glob.glob(os.path.join(data_dir, "*.log"))
    return max(logs, key=os.path.getmtime) if logs else None


def eventi_previsti(path, n_passi, secondi, rate_max=1000):
    """Avvisa PRIMA di partire se la run finira' a NEvents a meta' scan.
    Il conto usa il rate massimo dello scan (~1000 Hz, la saturazione del
    V1742): a bassa soglia si va li'."""
    log = path[:-3] + ".log" if path.endswith(".h5") else None
    try:
        n = re.search(r"(?m)^NEvents\s*=\s*(\d+)", open(log, errors="replace").read())
    except (OSError, TypeError):
        return
    if not n:
        return
    restano = int(n.group(1)) - n_events(path)
    servono = int(n_passi * (secondi + 2) * rate_max)
    if restano < servono:
        print(f"ATTENZIONE: alla run restano {restano} eventi prima di NEvents = {n.group(1)}; "
              f"a {rate_max:g} Hz lo scan ne puo' chiedere fino a {servono}.\n"
              "            La run potrebbe chiudersi a meta' scan: alza NEvents nel TOML e "
              "riavvia la run.\n")


def predicted(distances, sigma):
    """Rate atteso sommando i canali, ognuno con la sua distanza vera."""
    return sum(F_ADC * 0.5 * math.erfc(d / (sigma * math.sqrt(2.0)))
               for d in distances)


def sigma_from_rate(distances, rate):
    """Sigma che riprodurrebbe il rate misurato, per bisezione."""
    if rate <= 0:
        return None
    lo, hi = 0.05, 5.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if predicted(distances, mid) < rate:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def larghezza_impulsi(path, canali, quanti=300):
    """FWHM mediana degli impulsi in coda al file, in ns. None se non si puo'.

    La si registra nella misura perche' e' lei a decidere quale calibrazione
    offset -> mV si applica: fra 1.8 e 189 ns il fattore cambia di otto volte,
    mentre fra 2.5 e 1 GS/s cambia del 25. Senza, chi disegna lo scan mesi
    dopo puo' solo indovinare -- e indovinare qui costa un fattore sei.

    Si leggono gli ultimi eventi e basta: il costo non deve crescere con la
    durata della run, e la larghezza non dipende dal punto dello scan.
    """
    try:
        # live=True serve mentre la run scrive; su un file gia' chiuso e
        # compresso daqio lo rifiuta, ed e' il caso quando si rilegge uno scan
        # vecchio. Si prova l'uno e si ripiega sull'altro.
        try:
            hdr, d = carica_run(path, last=quanti, live=True,
                                channels=canali or None, strict=False)
        except Exception:
            hdr, d = carica_run(path, last=quanti,
                                channels=canali or None, strict=False)
        if d is None or d.shape[0] < 20:
            return None
        # SamplingTime sta nel file in SECONDI; qui si lavora in nanosecondi
        # come tutto il resto del progetto.
        dt = (float(hdr.get("SamplingTime") or 0) or 0.0) * 1e9
        if not dt:
            return None
        t = np.arange(d.shape[2]) * dt
        i0, i1 = int(10.0 / dt), int(180.0 / dt)
        if i1 - i0 < 5:
            return None
        base = np.median(d[:, 0, i0:i1], axis=1)
        w = d[:, 0, :] - base[:, None]
        # Il verso dal log della run (DCOffset per canale, poi TriggerPolarity),
        # come monitor ed efficienza. Dedurlo dal segno della somma delle
        # tracce e' fragile con i PMT amplificati: impulso negativo e coda
        # positiva che lo segue hanno aree quasi uguali, e la somma resta
        # vicina a zero. Resta come ripiego se il log non dice niente.
        # NB: la larghezza e' misurata sugli ULTIMI eventi, quindi alla soglia
        # di fine scan: i PMT amplificati sono larghi 7 ns sotto ~100 mV e
        # 9 ns sopra 200, e a fine scan restano solo i grandi.
        segno = None
        try:
            import efficienza
            ch0 = int(hdr["ChannelList"][0])
            base_log = path[:-3] if path.endswith(".gz") else path
            v = efficienza._segni_per([ch0], efficienza.polarita_dal_log(
                os.path.splitext(base_log)[0] + ".log"))
            segno = float(v[0]) if v is not None else None
        except Exception:
            segno = None
        w = w * (segno if segno is not None else (np.sign(np.sum(w)) or 1.0))

        # La larghezza si misura EVENTO PER EVENTO, attorno al massimo di
        # ciascuno, e poi se ne prende la mediana. Misurarla sulla forma
        # d'onda media sembra piu' naturale ed e' sbagliato: gli impulsi non
        # sono allineati nel tempo -- sugli impulsi al buio di un SiPM il
        # picco della mediana e' 20 conteggi contro i 48 di ampiezza tipica,
        # cioe' la media li impasta -- e la larghezza che ne usciva ballava
        # fra 9 e 61 ns a seconda di quanti eventi si guardavano.
        rumore = float(np.median(np.std(w[:, i0:i1], axis=1))) or 1.0
        larghezze = []
        for riga in w:
            pk = float(riga.max())
            if pk < 5 * rumore:                  # senza impulso non c'e' larghezza
                continue
            ipk = int(riga.argmax())
            a = np.where(riga[:ipk] < pk / 2)[0]
            b = np.where(riga[ipk:] < pk / 2)[0]
            ta = t[a[-1]] if len(a) else t[0]
            tb = t[ipk + b[0]] if len(b) else t[-1]
            if tb > ta:
                larghezze.append(tb - ta)
        if len(larghezze) < 10:
            return None
        return round(float(np.median(larghezze)), 1)
    except Exception:
        # Una larghezza che non si riesce a misurare non deve far fallire uno
        # scan: e' un di piu', e il campo resta assente.
        return None


def scrivi_misura(args, path, st, channels, sigma, rows, iniziali=None, interrotto=None):
    """Misura salvata per intero, cosi' due scan si possono confrontare davvero."""
    rec = {
        "file": os.path.basename(path),
        "quando": time.strftime("%Y-%m-%d %H:%M:%S"),
        "canali": channels,
        "baseline": {str(int(c["ch"])): c["baseline"] for c in st["channels"]},
        "sigma_assunto": None if relativa(st) else sigma,
        "secondi_per_punto": args.seconds,
        "larghezza_fwhm_ns": larghezza_impulsi(path, channels),
        "punti": [{"offset": r[0], "distanza": r[1], "rate": r[2],
                   "atteso": r[3], "conteggi": r[4],
                   "eventi_da": r[5], "eventi_a": r[6]} for r in rows],
    }
    nome = "noise_scan_%s" % time.strftime("%Y%m%d_%H%M%S")
    if SCHEDA:
        # Chi legge il JSON mesi dopo deve sapere che unita' ha l'asse: con una
        # soglia relativa i valori sono conteggi ADC dalla base, non offset.
        rec.update({"scheda": SCHEDA, "modello": st.get("model"),
                    "soglia_relativa": relativa(st),
                    "unita_soglia": ("ADC counts from the baseline" if relativa(st)
                                     else "offset counts"),
                    "offset_iniziali": {str(c): o for c, o in (iniziali or {}).items()},
                    "logica": args.logica, "maggioranza": args.maggioranza,
                    "interrotto": interrotto})
        nome += "_" + SCHEDA
    if args.canale is not None:
        # Col segnale acceso il rate "atteso" dal rumore gaussiano non vuol
        # dire niente: resta nei punti per chi legge il formato, ma si dice.
        rec.update({"canale_scansionato": args.canale,
                    "parcheggio": args.parcheggio,
                    "offset_iniziali": {str(c): o for c, o in iniziali.items()},
                    "segnale_acceso": True,
                    "interrotto": interrotto})
        nome += "_ch%d" % args.canale
    os.makedirs(args.out, exist_ok=True)
    rec_path = os.path.join(args.out, nome + ".json")
    with open(rec_path, "w") as f:
        json.dump(rec, f, indent=2)
    if args.canale is not None:
        print(f"misura : {rec_path}", flush=True)
    return rec_path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offsets", type=float, nargs="+",
                    default=[6, 5, 4, 3, 2],
                    help="offset da provare, meglio in ordine decrescente")
    ap.add_argument("--seconds", type=float, default=20,
                    help="secondi di misura per punto (default 20)")
    ap.add_argument("--sigma", type=float, default=None,
                    help="rumore in Transparent Mode [conteggi], per la previsione. "
                         "Se omesso si usa quello nel file di stato")
    ap.add_argument("-d", "--data-dir", default=os.path.join(ROOT, "data"))
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "plots"))
    ap.add_argument("--canale", type=int, default=None,
                    help="scan di UN canale, col segnale acceso: gli altri canali del "
                         "trigger restano a --parcheggio e a fine scan tornano tutti "
                         "agli offset di partenza")
    ap.add_argument("--parcheggio", type=float, default=None,
                    help="offset dei canali fermi con --canale (default 4095: la soglia "
                         "va al massimo del registro e quei canali non scattano; con una "
                         "scheda a soglia relativa %d conteggi)" % PARCHEGGIO_RELATIVO)
    ap.add_argument("--scheda", default=None,
                    help="la scheda del controllore a schede: file di stato e di comando "
                         "con il suo nome (live-status-<scheda>.json)")
    ap.add_argument("--logica", default=None,
                    help="logica del trigger della scheda (OR, AND, Majority), solo da "
                         "registrare nel JSON: il rate di tutti i canali insieme e' quello")
    ap.add_argument("--maggioranza", type=int, default=None,
                    help="livello di maggioranza, con --logica Majority (solo registrato)")
    ap.add_argument("--rate-max", type=float, default=None,
                    help="rate oltre il quale lo scan si ferma [Hz] (default 5000; 20000 "
                         "con una scheda a soglia relativa)")
    args = ap.parse_args()

    global SCHEDA
    SCHEDA = args.scheda
    try:
        st = status(args.data_dir)
    except OSError as e:
        sys.exit(f"Non trovo lo stato della DAQ: {e}")
    rel = relativa(st)
    if args.parcheggio is None:
        args.parcheggio = PARCHEGGIO_RELATIVO if rel else 4095
    rate_max = args.rate_max or (20000 if rel else 5000)

    # Il file dei dati: quello che la scheda dichiara nel suo stato. Nella
    # cartella ci sono anche i file delle altre schede dello stesso run, e il
    # piu' recente potrebbe essere di un'altra.
    if SCHEDA and st.get("file"):
        path = os.path.join(args.data_dir, st["file"])
        if not os.path.exists(path):
            sys.exit(f"La scheda {SCHEDA} dichiara il file {st['file']}, che in "
                     f"{args.data_dir} non c'e'.")
    else:
        files = glob.glob(os.path.join(args.data_dir, f"*_{SCHEDA}_*.h5" if SCHEDA else "*.h5"))
        if not files:
            sys.exit(f"Nessuna run in corso: nessun file .h5 in {args.data_dir}")
        path = max(files, key=os.path.getmtime)

    if rel:
        # Una soglia relativa si scrive in conteggi interi, e la scheda rifiuta
        # quello che esce dalla dinamica: meglio dirlo prima del primo punto.
        for v in args.offsets + [args.parcheggio]:
            if v != int(v) or not 1 <= v <= PARCHEGGIO_RELATIVO:
                sys.exit(f"Soglia {v:g}: per {st.get('model', 'questa scheda')} serve un "
                         f"intero fra 1 e {PARCHEGGIO_RELATIVO} (conteggi ADC dalla base).")

    tutti = [int(c["ch"]) for c in st["channels"]]
    iniziali = {int(c["ch"]): c["offset"] for c in st["channels"]}
    if args.canale is not None and args.canale not in tutti:
        sys.exit(f"ch{args.canale} non partecipa al trigger di questa run ({tutti}).")
    channels = tutti if args.canale is None else [args.canale]
    sigma = args.sigma or 0.72

    # Senza baseline registrata due scan non sono confrontabili: la "distanza"
    # e' baseline meno soglia, e la baseline si misura una volta sola all'avvio
    # della run. Se si sposta fra una run e l'altra -- per esempio perche' e'
    # cambiata la corrente di anodo -- distanze nominalmente uguali non lo sono.
    print(f"file   : {os.path.basename(path)}")
    if rel:
        print(f"scheda : {SCHEDA} ({st.get('model', '?')}), soglia relativa alla base "
              f"in conteggi ADC, fronte {st.get('polarity', '?')}")
        print(f"canali : {channels}")
    else:
        print(f"canali : {channels}      sigma assunto: {sigma} conteggi")
        print(f"baseline all'avvio della run (da {_nome('live-status.json')}):")
        for c in st["channels"]:
            print(f"   ch{int(c['ch'])}: {c['baseline']:.3f}")
    print()
    eventi_previsti(path, len(args.offsets), args.seconds, 10000 if rel else 1000)
    # Le soglie si rimettono come erano anche nello scan di tutti i canali,
    # per una scheda: col VX2730B non c'e' una misura del piedistallo che
    # le ricalcoli al run dopo, e la run resterebbe all'ultimo punto.
    ripristina = args.canale is not None or SCHEDA is not None
    if args.canale is not None:
        fermi = [c for c in tutti if c != args.canale]
        print(f"scan del solo ch{args.canale}: {', '.join('ch%d' % c for c in fermi)} "
              f"a {'soglia' if rel else 'offset'} {args.parcheggio:g}")
    elif not rel:
        print("ATTENZIONE: il segnale deve essere spento. Se i PMT sono attivi,")
        print("            quello che misuri e' segnale piu' rumore.\n")
    else:
        print("tutti i canali del trigger insieme, con la stessa soglia")
    if ripristina:
        print(("soglie" if rel else "offset") + " di partenza, rimesse a fine scan: "
              + ", ".join(f"ch{c} {o:g}" for c, o in iniziali.items()) + "\n")
        # SIGTERM e' quello che manda "Stop scan" della pagina di controllo:
        # deve passare dal ripristino degli offset come la fine normale, se no
        # la run resta con quattro canali su cinque spenti.
        signal.signal(signal.SIGTERM, lambda *_: sys.exit("scan interrotto (SIGTERM)"))
    # Col segnale acceso il modello del rumore non c'entra: le sue due colonne
    # sarebbero numeri senza significato accanto a quelli misurati. Nemmeno
    # con il VX2730B: il modello e' quello del comparatore del V1742, che
    # campiona a ~30 MHz in Transparent Mode.
    rumore = args.canale is None and not rel
    print(f"  {'soglia' if rel else 'offset':>7} {'scritte' if rel else 'soglie':>13} "
          f"{'distanza':>10} {'rate misurato':>16} "
          + (f"{'atteso':>11} {'sigma implicito':>16}" if rumore else f"{'eventi':>9}"))

    rows = []
    prev_thr = None
    interrotto = None
    try:
        for k, off in enumerate(args.offsets, 1):
            # Marcatore di avanzamento, letto dalla pagina di controllo. Senza,
            # da fuori non si distingue uno scan che sta lavorando da uno
            # piantato: la tabella stampa una riga solo a punto FINITO, e con
            # 20 s per punto sono venti secondi di silenzio.
            quale = "" if args.canale is None else f"ch{args.canale} "
            print(f"[punto {k}/{len(args.offsets)}] {quale}{'threshold' if rel else 'offset'} "
                  f"{off:g}", flush=True)
            if args.canale is None:
                applied = set_offset(args.data_dir, channels, int(off) if rel else off)
            else:
                applied = set_offsets(args.data_dir, {c: (off if c == args.canale
                                                          else args.parcheggio)
                                                      for c in tutti})
            thr = tuple(applied[ch][1] for ch in channels)
            # Il valore ASSOLUTO: la soglia sta sotto il piedistallo con gli
            # impulsi negativi e sopra con quelli positivi, e la coda gaussiana
            # non sa distinguere i due casi -- e' la stessa coda vista dai due
            # lati. Senza abs(), col fronte di salita le distanze venivano
            # negative, erfc() tendeva a 2 e il rate atteso usciva saturo a
            # 3e7 Hz su tutta la riga, mentre il sigma implicito non si poteva
            # nemmeno calcolare. Il verso e' registrato altrove (campo
            # "polarity" di live-status.json), qui non aggiunge niente.
            # Con la soglia relativa la base non si conosce (null): la distanza
            # dalla base e' la soglia stessa.
            dists = [abs(applied[ch][1]) if applied[ch][0] is None
                     else abs(applied[ch][0] - applied[ch][1]) for ch in channels]

            if thr == prev_thr:
                print(f"  {off:7g} {str(thr):>13}   COLLASSA sulla soglia precedente, salto")
                continue
            prev_thr = thr

            time.sleep(1.0)                        # la soglia entra in vigore
            # Gli estremi vanno registrati, non ricostruiti dopo: dedurli da
            # rate x durata sbaglia appena la DAQ perde eventi o il rate varia
            # dentro il passo, e l'analisi finisce per campionare il segmento
            # sbagliato senza accorgersene.
            start = n_events(path)
            t0 = time.time()
            time.sleep(args.seconds)
            dt = time.time() - t0
            counts = n_events(path) - start
            rate = counts / dt

            # Zero conteggi non e' "rate nullo": e' un limite superiore
            meas = (f"{rate:10.1f} Hz" if counts > 0
                    else f"  < {3.0/dt:5.2f} Hz")
            exp = predicted(dists, sigma)
            exp_s = f"{exp:8.1f} Hz" if exp < 1e4 else "  saturo"
            sfit = sigma_from_rate(dists, rate)
            sfit_s = f"{sfit:14.2f}" if sfit else "             -"

            print(f"  {off:7g} {str(thr):>13} {np.mean(dists):9.2f} {meas:>16} "
                  + (f"{exp_s:>11} {sfit_s}" if rumore else f"{counts:9d}"), flush=True)
            rows.append((off, np.mean(dists), rate, exp, counts, start, start + counts))

            if rate > rate_max:
                print(f"     rate oltre {rate_max:g} Hz: mi fermo qui per non intasare la DAQ")
                break
    except (SystemExit, KeyboardInterrupt) as e:
        if not ripristina:
            raise
        interrotto = e
    finally:
        if ripristina:
            try:
                set_offsets(args.data_dir, iniziali)
                print(("soglie di partenza rimesse: " if rel else "offset di partenza rimessi: ")
                      + ", ".join(f"ch{c} {o:g}" for c, o in iniziali.items()), flush=True)
            except SystemExit as e:
                print(f"ATTENZIONE: offset di partenza NON rimessi. {e.code}", flush=True)

    if ripristina:
        # I punti misurati valgono anche se lo scan e' stato interrotto: si
        # registrano, e il JSON dice che il canale non e' completo.
        if rows:
            scrivi_misura(args, path, st, channels, sigma, rows, iniziali,
                          None if interrotto is None else str(getattr(interrotto, "code", "") or "interrotto"))
        if interrotto is not None:
            raise interrotto
        return

    # --- grafico ---
    os.makedirs(args.out, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    x = np.array([r[1] for r in rows])                 # distanza vera
    y = np.array([max(r[2], 1e-3) for r in rows])
    det = np.array([r[4] > 0 for r in rows])
    ax.semilogy(x[det], y[det], "o-", label="misurato")
    if (~det).any():
        ax.semilogy(x[~det], y[~det], "v", color="grey", label="limite superiore")

    xs = np.linspace(min(x) * 0.9, max(x) * 1.1, 100)
    ys = [max(predicted([v] * len(channels), sigma), 1e-3) for v in xs]
    ax.semilogy(xs, ys, "--", label=f"coda gaussiana, σ = {sigma}")

    fitted = [sigma_from_rate([r[1]] * len(channels), r[2]) for r in rows if r[4] > 0]
    if fitted:
        sm = float(np.median(fitted))
        ax.semilogy(xs, [max(predicted([v] * len(channels), sm), 1e-3) for v in xs],
                    ":", label=f"σ implicito dai dati = {sm:.2f}")

    ax.set_xlabel("distanza fra piedistallo e soglia [conteggi]")
    ax.set_ylabel("rate di trigger [Hz]")
    ax.set_title("Pavimento di rumore del self-trigger", fontsize=11)
    ax.grid(alpha=.3, which="both")
    ax.legend()
    fig.tight_layout()
    out = os.path.join(args.out, "noise_scan.png")
    fig.savefig(out, dpi=120)
    plt.close(fig)

    rec_path = scrivi_misura(args, path, st, channels, sigma, rows)
    print(f"\ngrafico: {out}")
    print(f"misura : {rec_path}")
    if fitted:
        print(f"\nsigma implicito dai punti misurati: {np.median(fitted):.2f} conteggi "
              f"(assunto {sigma})")
        print("Se i valori nella colonna 'sigma implicito' concordano fra loro, il")
        print("modello gaussiano regge e quel numero e' il rumore vero del")
        print("comparatore. Se divergono, la coda non e' gaussiana.")


if __name__ == "__main__":
    main()
