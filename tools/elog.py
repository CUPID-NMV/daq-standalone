#!/usr/bin/env python3
"""
Le righe automatiche dell'e-log, una per run.

    python3 tools/elog.py                       # CSV su stdout
    python3 tools/elog.py -o /tmp/runs_auto.csv
    python3 tools/elog.py --da 2026-10-01

Si legge il **log testuale** di ogni run, non l'HDF5: l'header di un file
compresso si ottiene solo decomprimendolo tutto, e con 174 run da 67 GB
sarebbero ore. Il .log ha gli stessi numeri ed e' istantaneo.

Queste colonne si rigenerano da zero ogni volta. Le colonne scritte a mano --
scopo, sorgente, setup, esito, note -- stanno in un ALTRO foglio, legato a
questo dal nome della run: se stessero nelle stesse righe, la prima
rigenerazione se le porterebbe via.
"""
import argparse
import csv
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANSI = re.compile(r"\x1b\[[0-9;]*m")

# L'ordine e' quello in cui si leggono: prima cos'e', poi com'era messa la
# macchina, poi quanto e' durata.
COLONNE = ["run", "data", "ora", "hv_v", "i_ua", "canali", "trigger",
           "soglie", "maggioranza", "campionamento", "finestra_ns",
           "post_trigger_pc", "eventi", "rate_hz", "durata_s", "chiusa",
           "operatore", "file", "byte"]


def _num(testo, patt, tipo=float, default=None):
    m = re.search(patt, testo)
    if not m:
        return default
    try:
        return tipo(m.group(1))
    except ValueError:
        return default


def leggi_avvii(path):
    """(istante, chi) di ogni avvio di run, dal registro del controllore.

    Chi ha lanciato la run lo sa gia' la pagina: lo scrivi nella casella "who
    are you" e finisce in data/azioni.jsonl a ogni avvio. Ricopiarlo a mano
    nell'e-log sarebbe esattamente il lavoro che questo strumento esiste per
    togliere.
    """
    fuori = []
    try:
        with open(path) as f:
            for riga in f:
                try:
                    v = json.loads(riga)
                except ValueError:
                    continue
                # Qualunque azione, non solo gli avvii: chi sta al banco
                # cambia la configurazione, rampa la tensione, lancia la coda.
                # Sono tutte firme sue, e in una coda che dura ore l'unico
                # avvio con un nome e' il primo.
                try:
                    t = time.mktime(time.strptime(v["quando"], "%Y-%m-%d %H:%M:%S"))
                except (KeyError, ValueError):
                    continue
                chi = (v.get("chi") or "").strip()
                if chi and chi != "(anonymous)":
                    fuori.append((t, chi))
    except OSError:
        return []
    fuori.sort()
    return fuori


def chi_ha_avviato(avvii, inizio):
    """Chi si e' fatto vedere per ultimo prima di questa run, lo stesso giorno.

    Una coda dura ore e appartiene a chi l'ha lanciata: una finestra di
    minuti attribuiva solo la prima run e lasciava vuote tutte le altre. Il
    limite e' il GIORNO di calendario, che e' anche come la gente ragiona --
    "ieri sera le ha lanciate Luigi" -- e impedisce che la firma di ieri
    finisca su una run di stamattina presto.
    """
    if not avvii or inizio is None:
        return ""
    giorno = time.strftime("%Y-%m-%d", time.localtime(inizio))
    candidati = [a for a in avvii
                 if a[0] <= inizio + 60
                 and time.strftime("%Y-%m-%d", time.localtime(a[0])) == giorno]
    return candidati[-1][1] if candidati else ""


def leggi_psu(path):
    """(istante, v_out, i_out) dal CSV dell'alimentatore, ordinati."""
    fuori = []
    try:
        with open(path) as f:
            for riga in csv.reader(f):
                if len(riga) < 5 or not riga[0].replace(".", "").isdigit():
                    continue
                try:
                    fuori.append((float(riga[0]), float(riga[2]), float(riga[4])))
                except ValueError:
                    continue
    except OSError:
        return []
    fuori.sort()
    return fuori


def hv_a(psu, quando, tolleranza=600.0):
    """Tensione e corrente al momento dato, se la lettura e' abbastanza vicina.

    Fuori tolleranza si lascia vuoto: una tensione presa un'ora prima non
    descrive questa run, e scriverla sarebbe peggio che non scriverla.
    """
    if not psu or quando is None:
        return "", ""
    migliore = min(psu, key=lambda r: abs(r[0] - quando))
    if abs(migliore[0] - quando) > tolleranza:
        return "", ""
    return round(migliore[1], 2), round(migliore[2] * 1e6, 2)


def riga_da_log(path_log, psu, avvii=()):
    """Una riga dell'e-log dal log di una run, oppure None se non e' una run."""
    try:
        testo = ANSI.sub("", open(path_log, errors="replace").read())
    except OSError:
        return None

    m = re.search(r"HDF5 output path selected:\s*(\S+)", testo)
    if not m:
        return None                      # run morta prima di aprire il file
    h5 = os.path.basename(m.group(1).replace("//", "/"))
    nome = h5[:-3] if h5.endswith(".h5") else h5

    canali = ", ".join(re.findall(r"^Summary :\s+ch(\d+)\s*$", testo, re.M))

    # Trigger: quale sorgente, e con che soglie. Le due provenienze NON si
    # mescolano -- quelle del CFD sono millivolt misurati, quelle del
    # self-trigger unita' di offset da convertire -- quindi la colonna dice
    # anche da dove viene il numero.
    # Due formulazioni nella storia del log: "Self-trigger: ENABLED" e
    # "Self-trigger ENABLED (mode = ...)". Cercare solo la prima faceva
    # risultare "software" centocinquanta run di self-trigger dei PMT.
    self_on = re.search(r"Self-trigger:?\s+ENABLED", testo) is not None
    est_on = "External trigger (TRG-IN): ENABLED" in testo
    cfd = re.search(r"CFD V812 configurato:\s*(.+)", testo)
    magg = _num(testo, r"maggioranza (\d+)", int, "")

    if cfd and est_on:
        trigger = "CFD (TRG-IN)"
        soglie = "%s (CFD, mV)" % re.sub(r"ch\d+ a ", "", cfd.group(1)).strip()
    elif self_on:
        trigger = "self-trigger"
        off = re.search(r"SelfTriggerThresholdOffset \[counts\]:\s*(.+)", testo)
        soglie = ("%s (self, offset)" % re.sub(r"ch\d+=", "", off.group(1)).strip()
                  if off else "")
        magg = ""
    elif est_on:
        trigger = "TRG-IN"
        soglie = ""
        magg = ""
    else:
        trigger = "software"
        soglie = ""
        magg = ""

    eventi = _num(testo, r"Events written to file:\s*(\d+)", int, "")
    durata = _num(testo, r"Acquisition time:\s*([0-9.]+)", float, "")
    rate = _num(testo, r"Trigger rate:\s*([0-9.]+) kHz", float, None)
    rate = round(rate * 1000.0, 2) if rate is not None else ""

    passo = _num(testo, r"Sampling frequency set to \S+ \(([0-9.]+) ns", float)
    rl = _num(testo, r"RecordLength\s*=\s*(\d+)", int)
    finestra = round(passo * rl) if (passo and rl) else ""

    camp = (re.search(r"Sampling frequency set to (\S+)", testo) or [None, ""])
    camp = camp.group(1) if hasattr(camp, "group") else ""
    pt = _num(nome, r"_(\d+)PT$", int, "")

    # L'istante: il mtime del log e' la FINE della run; l'inizio si ricava
    # togliendo la durata, che e' quella misurata dalla DAQ.
    fine = os.path.getmtime(path_log)
    inizio = fine - (durata or 0)
    v, i = hv_a(psu, inizio)

    h5path = os.path.join(os.path.dirname(path_log), h5)
    byte = ""
    for p in (h5path, h5path + ".gz"):
        if os.path.exists(p):
            byte = os.path.getsize(p)
            break

    return {
        "run": nome, "data": time.strftime("%Y-%m-%d", time.localtime(inizio)),
        "ora": time.strftime("%H:%M:%S", time.localtime(inizio)),
        "hv_v": v, "i_ua": i, "canali": canali, "trigger": trigger,
        "soglie": soglie, "maggioranza": magg, "campionamento": camp,
        "finestra_ns": finestra, "post_trigger_pc": pt, "eventi": eventi,
        "rate_hz": rate, "durata_s": round(durata, 1) if durata != "" else "",
        # Una run il cui log si ferma ad "Acquisition started" e' stata
        # interrotta: niente riepilogo finale, quindi niente eventi ne' rate.
        # E' un'informazione, non un buco, e va detta.
        # Tre stati, non due: una run IN CORSO non ha ancora il riepilogo
        # finale e sembrerebbe interrotta. Si distingue dal fatto che il suo
        # .h5 non compresso esiste ancora ed e' stato scritto da poco.
        "operatore": chi_ha_avviato(avvii, inizio),
        "chiusa": ("si" if eventi != ""
                   else ("in corso" if (os.path.exists(h5path) and
                                        time.time() - os.path.getmtime(h5path) < 120)
                         else "NO")),
        "file": os.path.basename(h5path) + (".gz" if byte and not os.path.exists(h5path) else ""),
        "byte": byte,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-d", "--data-dir", default=os.path.join(ROOT, "data"))
    ap.add_argument("-o", "--out", default=None, help="CSV di uscita (default: stdout)")
    ap.add_argument("--da", default=None, help="solo run dal giorno AAAA-MM-GG in poi")
    ap.add_argument("-n", "--ultime", type=int, default=None,
                    help="solo le ultime N run (le prime sono quasi sempre prove)")
    a = ap.parse_args()

    psu = leggi_psu(os.path.join(a.data_dir, "psu-log.csv"))
    avvii = leggi_avvii(os.path.join(a.data_dir, "azioni.jsonl"))
    righe = []
    for radice, _, files in os.walk(a.data_dir):
        for f in files:
            if f.endswith(".log") and not f.startswith("daq-console"):
                r = riga_da_log(os.path.join(radice, f), psu, avvii)
                if r and (not a.da or r["data"] >= a.da):
                    righe.append(r)
    righe.sort(key=lambda r: (r["data"], r["ora"]))
    # Le ultime N, non le prime: un e-log parte da quando si e' smesso di
    # provare, e le prove restano sul disco per chi le vuole.
    if a.ultime:
        righe = righe[-a.ultime:]

    uscita = open(a.out, "w", newline="") if a.out else sys.stdout
    w = csv.DictWriter(uscita, fieldnames=COLONNE)
    w.writeheader()
    w.writerows(righe)
    if a.out:
        uscita.close()
        print("%d run in %s" % (len(righe), a.out), file=sys.stderr)


if __name__ == "__main__":
    main()
