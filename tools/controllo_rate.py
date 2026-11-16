#!/usr/bin/env python3
"""Il controllo del rate ogni ora: configurazione di riferimento, misura, allarme.

Ogni ora il controllore (tools/daq_control.py) ferma il run in corso, prende
un run di 2 minuti con una configurazione SEMPRE UGUALE -- quella dei run che
disegna tools/Andamento_run.ipynb: self-trigger sui cinque canali con le loro
soglie, senza trigger esterno ne' V812 -- e poi rimette il TOML e fa ripartire
il run di prima. Il rate di quei due minuti, confrontato con quello atteso,
dice se il rivelatore e l'elettronica sono rimasti come erano, qualunque cosa
stia facendo nel frattempo il run di fisica (oggi i cosmici in maggioranza 5).

Qui sta tutto quello che non ha bisogno del controllore, cosi' si prova sul
Mac: la configurazione di riferimento, quando tocca al prossimo controllo, la
misura del rate da un run finito, l'allarme e il registro. Il controllore fa
la parte che tocca la DAQ: fermare, applicare, avviare, rimettere.

    python3 tools/controllo_rate.py --misura data/LNGS/check/CHECK__54-0V_0000_750Ms_55PT.log
    python3 tools/controllo_rate.py --stato
"""

import argparse
import json
import math
import os
import re
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATO = os.path.join(ROOT, "data", "controllo-rate.json")
REGISTRO = os.path.join(ROOT, "data", "controllo-rate.jsonl")
CALIBRAZIONE = os.path.join(ROOT, "measurements", "calib_selftrigger_20261030_ch%d.json")
MV_PER_CONTEGGIO = 1000.0 / 4096.0

# La sottocartella di OutputDir dove vanno i run di controllo. Non mescolati ai
# run di fisica: l'e-log e il riepilogo serale li saltano (sono UNA misura
# ripetuta, non 24 run al giorno da elencare), archivia.py li manda sul cloud.
CARTELLA = "check"

# Le chiavi che fanno la configurazione di un run: trigger, canali,
# campionamento, correzioni. Sono quelle che il controllo impone ogni volta.
# NON ci sono OutputDir, OutputFile, NEvents, SaveData: quelle le decide il
# controllo stesso (cartella check/, prefisso suo, abbastanza eventi, dati
# salvati anche se il run di fisica era di prova). Ne' i livelli del pannello
# frontale e il collegamento, che non cambiano da un run all'altro.
CHIAVI = [("digitizer", k) for k in (
    "ChannelList", "DCOffset", "DRS4Correction", "ExternalTrigger", "OutputFormat",
    "PostTriggerSize", "RecordLength", "SamplingRate", "SelfTrigger", "SelfTriggerAcquire",
    "SelfTriggerChannels", "SelfTriggerMode", "SelfTriggerThreshold",
    "SelfTriggerThresholdMode", "SelfTriggerThresholdOffset", "SoftwareTrigger",
    "TransparentDump", "TriggerOut", "TriggerPolarity")] + [("cfd", "Enabled")]

# La configurazione dei run di Andamento_run, copiata dal TOML che la DAQ
# scrive in testa al .log di LNGS___54-0V_0001_750Ms_55PT (2026-11-11): 377 dei
# run LNGS__54-0V e LNGS___54-0V hanno queste soglie (offset 450/600/600/80/80),
# gli altri sono prove di poche run. Valori come letterali TOML, quelli che
# tomledit scrive nel file.
RIFERIMENTO_ANDAMENTO = [
    ["digitizer", "ChannelList", "[16, 17, 18, 20, 21]"],
    ["digitizer", "DCOffset", "0xB000"],
    ["digitizer", "DRS4Correction", "true"],
    ["digitizer", "ExternalTrigger", "false"],
    ["digitizer", "OutputFormat", '"HDF5"'],
    ["digitizer", "PostTriggerSize", "55"],
    ["digitizer", "RecordLength", "1024"],
    ["digitizer", "SamplingRate", '"750MHz"'],
    ["digitizer", "SelfTrigger", "true"],
    ["digitizer", "SelfTriggerAcquire", "true"],
    ["digitizer", "SelfTriggerChannels", "[16, 17, 18, 20, 21]"],
    ["digitizer", "SelfTriggerMode", '"paired"'],
    ["digitizer", "SelfTriggerThreshold", "[2000]"],
    ["digitizer", "SelfTriggerThresholdMode", '"relative"'],
    ["digitizer", "SelfTriggerThresholdOffset", "[450, 600, 600, 80, 80]"],
    ["digitizer", "SoftwareTrigger", "false"],
    ["digitizer", "TransparentDump", "false"],
    ["digitizer", "TriggerOut", '"off"'],
    ["digitizer", "TriggerPolarity", "0"],
    ["cfd", "Enabled", "false"],
]
ORIGINE_ANDAMENTO = ("the runs of Andamento_run (LNGS__54-0V, LNGS___54-0V): self-trigger "
                     "on ch16-18 and ch20-21, offsets 450/600/600/80/80, no external trigger, V812 off")

# Il rate atteso: mediana dei 60 run standard piu' recenti nel grafico di
# Andamento_run (12-13/11, measurements/andamento_..._449run.json): 47.29 Hz,
# fra 46.57 e 49.00, dispersione da un run all'altro dell'1.3%. In 120 s sono
# ~5700 eventi, cioe' un errore statistico dell'1.3% per controllo: la soglia
# del 10% ne e' lontana sette sigma.
ATTESO_ANDAMENTO = 47.3
ORIGINE_ATTESO = "median of the 60 most recent runs of Andamento_run (12-13/11, spread 1.3%)"

PREDEFINITI = {
    "attivo": False,         # si accende dalla pagina: da solo non ferma niente
    "ogni_min": 60,
    "al_minuto": 0,
    "durata_s": 120,
    "prefisso": "CHECK_",    # il controllore ci aggiunge la tensione: CHECK__54-0V
    "riferimento": RIFERIMENTO_ANDAMENTO,
    "riferimento_da": ORIGINE_ANDAMENTO,
    "riferimento_t": 0.0,    # da quando vale: i controlli prima non fanno l'atteso
    "atteso_hz": ATTESO_ANDAMENTO,
    "atteso_da": ORIGINE_ATTESO,
    "tolleranza": 0.10,
    "consecutivi": 2,
    # stato, non configurazione
    "ultimo_slot": 0.0,      # l'ultimo istante programmato gia' servito
    "fuori": 0,              # controlli consecutivi fuori tolleranza
    "allarme": False,        # mail d'allarme mandata e non ancora rientrata
    "falliti": 0,            # controlli falliti di fila (DAQ non partita, niente eventi)
    "in_corso": None,        # il controllo a meta': per riprendersi da un riavvio
}


# -- stato -------------------------------------------------------------------
def leggi(percorso=STATO):
    """La configurazione e lo stato, coi valori predefiniti dove mancano."""
    try:
        with open(percorso) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    out = json.loads(json.dumps(PREDEFINITI))
    out.update({k: v for k, v in d.items() if k in PREDEFINITI})
    return out


def scrivi(d, percorso=STATO, scrivi_file=None):
    """Su file con scrittura sicura (temporaneo e rename): un controllo a meta'
    deve poter essere ripreso dopo un riavvio, e un file troncato no."""
    testo = json.dumps(d, indent=1)
    if scrivi_file:
        scrivi_file(percorso, testo)
        return
    tmp = "%s.tmp-%d" % (percorso, os.getpid())
    with open(tmp, "w") as f:
        f.write(testo)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, percorso)


def letterale(v):
    """Un valore Python come letterale TOML, quello che tomledit scrive."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(letterale(x) for x in v) + "]"
    raise ValueError("value not representable in the TOML: %r" % (v,))


def riferimento_da_config(d):
    """Le CHIAVI di un TOML gia' letto (tomllib), come lista di modifiche."""
    out = []
    for sezione, chiave in CHIAVI:
        if chiave in d.get(sezione, {}):
            out.append([sezione, chiave, letterale(d[sezione][chiave])])
    return out


# -- quando ------------------------------------------------------------------
def _mezzanotte(t):
    lt = time.localtime(t)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def _istanti(m0, ogni_min, al_minuto):
    """Gli istanti programmati del giorno che comincia alla mezzanotte m0:
    al_minuto, al_minuto + ogni_min, ... fino a mezzanotte. Con un intervallo
    che non divide 1440 la serie riparte da al_minuto ogni giorno."""
    k = int(math.floor((1440 - 1e-6 - al_minuto) / ogni_min))
    return [m0 + (al_minuto + i * ogni_min) * 60.0 for i in range(k + 1)]


def slot_corrente(t, ogni_min, al_minuto):
    """L'ultimo istante programmato non dopo t (con 60 e 0: l'ultima ora piena)."""
    m0 = _mezzanotte(t)
    for giorno in (m0, _mezzanotte(m0 - 3600)):
        for s in reversed(_istanti(giorno, ogni_min, al_minuto)):
            if s <= t:
                return s
    return m0


def prossimo_slot(t, ogni_min, al_minuto):
    """Il primo istante programmato dopo t."""
    m0 = _mezzanotte(t)
    # +3600: con l'ora legale un giorno dura 23 o 25 ore.
    for giorno in (m0, _mezzanotte(m0 + 86400 + 3600)):
        for s in _istanti(giorno, ogni_min, al_minuto):
            if s > t:
                return s
    return m0 + 86400


def tocca(d, t):
    """Se e' l'ora di un controllo: (True, slot) quando l'istante programmato
    piu' recente non e' ancora stato servito. Dopo una lunga assenza (controllore
    spento) se ne fa uno solo, non tutti quelli persi."""
    if not d.get("attivo"):
        return False, None
    s = slot_corrente(t, int(d["ogni_min"]), int(d["al_minuto"]))
    return s > float(d.get("ultimo_slot") or 0), s


# -- misura ------------------------------------------------------------------
def leggi_log(log):
    """Eventi scritti, tempo di acquisizione e offset del self-trigger dal .log."""
    testo = open(log, errors="replace").read()
    n = re.search(r"Events written to file:\s*(\d+)", testo)
    t = re.search(r"Acquisition time:\s*([0-9.]+)", testo)
    o = re.search(r"SelfTriggerThresholdOffset \[counts\]:\s*(.*)", testo)
    return {"eventi": int(n.group(1)) if n else None,
            "durata_s": float(t.group(1)) if t else None,
            "offset": {int(c): int(v) for c, v in re.findall(r"ch(\d+)=(\d+)", o.group(1))} if o else {}}


def soglia_conteggi(canale, offset):
    """La soglia del self-trigger di un canale in conteggi, come in
    Andamento_run: offset per la calibrazione del 2026-10-30 (pendenza e
    intercetta in mV). None senza calibrazione."""
    f = CALIBRAZIONE % canale
    if offset is None or not os.path.exists(f):
        return None
    fit = json.load(open(f))["fit"]
    return (fit["pendenza_mv_per_offset"] * offset + fit["intercetta_mv"]) / MV_PER_CONTEGGIO


def misura(log, ped_ns=(0, 150), impulso_ns=(200, 600)):
    """Il rate di un run di controllo finito, dal suo .log e dal suo file.

    Totale: eventi scritti diviso il tempo di acquisizione dichiarato dalla DAQ
    (non i 120 s del controllore, che contano anche l'avvio). Per canale: eventi
    al secondo in cui il canale supera la soglia del suo self-trigger, con la
    definizione di Andamento_run (massimo nella finestra dell'impulso meno la
    mediana della finestra del piedistallo, nel verso del canale): cosi' i due
    grafici si confrontano. Il conto per canale si salta se il file non c'e'.
    """
    info = leggi_log(log)
    r = {"run": os.path.splitext(os.path.basename(log))[0], "log": log,
         "eventi": info["eventi"], "durata_s": info["durata_s"], "rate_hz": None,
         "errore_rel": None, "per_canale": {}, "soglie": {}}
    if not info["eventi"] or not info["durata_s"]:
        r["errore"] = ("no events written" if info["eventi"] == 0 else
                       "no end-of-run summary in the log: the run did not close")
        return r
    r["rate_hz"] = info["eventi"] / info["durata_s"]
    r["errore_rel"] = 1.0 / math.sqrt(info["eventi"])
    base = os.path.splitext(log)[0]
    dati = next((p for p in (base + ".h5", base + ".h5.gz") if os.path.exists(p)), None)
    if not dati or not info["offset"]:
        return r
    try:
        import numpy as np
        import daqio
        import efficienza
        versi = efficienza.polarita_dal_log(log)
        f, tmp = daqio.open_file(dati)
        try:
            hdr = daqio.read_header(f)
            nel_file = [int(c) for c in hdr["ChannelList"]]
            canali = [c for c in sorted(info["offset"]) if c in nel_file]
            ns, dt = int(hdr["SamplesPerChannel"]), float(hdr["SamplingTime"]) * 1e9
            p0, p1, i0, i1 = (int(round(x / dt)) for x in ped_ns + impulso_ns)
            ds = f["/events/waveforms"]
            sopra = {c: 0 for c in canali}
            soglie = {c: soglia_conteggi(c, info["offset"][c]) for c in canali}
            for a in range(0, ds.shape[0], 5000):
                w = ds[a:a + 5000].reshape(-1, len(nel_file), ns)
                for c in canali:
                    if soglie[c] is None:
                        continue
                    v = versi.get(c, 1) if isinstance(versi, dict) else (versi or 1)
                    x = w[:, nel_file.index(c), :].astype(np.float32)
                    s = (x - np.median(x[:, p0:p1], axis=1)[:, None]) * v
                    sopra[c] += int((s[:, i0:i1].max(axis=1) > soglie[c]).sum())
        finally:
            f.close()
            daqio._cleanup(tmp)
        r["per_canale"] = {str(c): sopra[c] / info["durata_s"] for c in canali if soglie[c] is not None}
        r["soglie"] = {str(c): soglie[c] for c in canali if soglie[c] is not None}
    except Exception as e:
        r["errore_canali"] = "%s: %s" % (type(e).__name__, e)
    return r


# -- allarme -----------------------------------------------------------------
def valuta(d, rate_hz):
    """Aggiorna contatori e allarme col rate di un controllo riuscito.

    Torna (scarto, evento): scarto relativo dal rate atteso (None se l'atteso
    non c'e'); evento "allarme" quando i controlli consecutivi fuori
    tolleranza arrivano a d["consecutivi"], "rientro" quando un controllo torna
    dentro dopo un allarme, None altrimenti. Una mail per evento, non una
    all'ora: chi la riceve sa gia' che c'e' qualcosa da guardare.
    """
    d["falliti"] = 0
    atteso = d.get("atteso_hz")
    if not atteso:
        return None, None
    scarto = rate_hz / float(atteso) - 1.0
    if abs(scarto) > float(d["tolleranza"]):
        d["fuori"] = int(d.get("fuori") or 0) + 1
        if d["fuori"] >= int(d["consecutivi"]) and not d.get("allarme"):
            d["allarme"] = True
            return scarto, "allarme"
        return scarto, None
    d["fuori"] = 0
    if d.get("allarme"):
        d["allarme"] = False
        return scarto, "rientro"
    return scarto, None


# -- registro ----------------------------------------------------------------
def annota(voce, percorso=REGISTRO):
    """Una riga JSON per controllo (si rilegge con pandas.read_json(lines=True))."""
    with open(percorso, "a") as f:
        f.write(json.dumps(voce) + "\n")


def storia(n=200, percorso=REGISTRO):
    """Gli ultimi n controlli, dal piu' vecchio."""
    try:
        with open(percorso) as f:
            righe = f.readlines()[-n:]
    except OSError:
        return []
    out = []
    for r in righe:
        try:
            out.append(json.loads(r))
        except ValueError:
            pass
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--misura", metavar="LOG", help="il rate di un run di controllo finito")
    ap.add_argument("--stato", action="store_true", help="configurazione e ultimi controlli")
    a = ap.parse_args()
    if a.misura:
        print(json.dumps(misura(a.misura), indent=1))
    elif a.stato:
        d = leggi()
        print(json.dumps({k: v for k, v in d.items() if k != "riferimento"}, indent=1))
        for v in storia(10):
            print("%s  %-32s %6s ev  %7s Hz  %s" % (v.get("ora"), v.get("run"), v.get("eventi"),
                                                    "%.2f" % v["rate_hz"] if v.get("rate_hz") else "-",
                                                    v.get("esito")))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
