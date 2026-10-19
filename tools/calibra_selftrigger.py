#!/usr/bin/env python3
"""
Calibra la soglia del self-trigger (offset -> mV) usando SOLO il digitizer.

    python3 tools/calibra_selftrigger.py plots/noise_scan_20261013_104413.json

Il generatore di impulsi da' UN punto, passante per l'origine per ipotesi, e
alla larghezza dell'impulso del generatore -- che non e' quella del rivelatore.
Confrontare lo scan del self-trigger con quello del CFD ne da' uno migliore,
alla larghezza giusta, ma misura il PRODOTTO fra la calibrazione e il rapporto
delle ampiezze ai due ingressi, e i due fattori non si separano.

Qui non serve ne' il generatore ne' il CFD: la scala in millivolt ce l'ha gia'
il digitizer, perche' registra le forme d'onda degli eventi che ha triggerato.

  - al passo j la DAQ conta R_j: e' il rate degli impulsi sopra la soglia T_j;
  - gli eventi del passo di riferimento (il piu' basso) sono un campione della
    distribuzione delle ampiezze sopra T_rif, misurate in mV;
  - quindi R_j/R_rif e' la frazione di quella distribuzione sopra T_j, e T_j e'
    il quantile corrispondente. In millivolt, assoluti.

Ogni passo dello scan diventa un punto, e la calibrazione e' la pendenza di una
retta su tutti i punti. Esce anche l'intercetta, che il metodo a un punto solo
non puo' vedere: se non e' compatibile con zero, la calibrazione "mV = a x
offset" e' sbagliata comunque la si misuri.

Il confine in eventi di ogni passo lo scrive noise_scan.py nel suo JSON: non si
deduce per aritmetica, che e' un errore gia' fatto in questo progetto.
"""
import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daqio                                             # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 1 Vpp su 12 bit. E' anche il passo del DAC di soglia, quindi il PAVIMENTO
# fisico della calibrazione: il Transparent Mode e' passivo e puo' solo
# attenuare, percio' un risultato sotto questo valore non e' una calibrazione
# piu' bassa, e' un'ipotesi sbagliata da qualche parte.
MV_PER_CONTEGGIO = 1000.0 / 4096.0

# I picchi spuri del V1742 sono simultanei su tutti i canali e con la stessa
# ampiezza (rapporto 1.02 +- 0.04). Si vetano cosi'; una coincidenza vera di
# rivelatori diversi non da' mai un rapporto cosi' stretto.
TOLLERANZA_SPURI = 0.05


def ampiezze_mV(dati, segni=None):
    """Ampiezza di ogni evento DALLA PARTE DELL'IMPULSO, in mV, massimo sui canali letti.

    `segni` e' il verso di ogni canale (+1 positivi, -1 negativi): i PMT hanno
    impulsi negativi e misurarne il massimo darebbe il rumore dal lato
    sbagliato (2026-10-18, primo scan dei PMT amplificati).

    Il piedistallo e' la mediana della traccia intera: qui e' lecito perche'
    l'impulso occupa ~35 campioni su 1024 (46 ns su 1365). NON lo sarebbe con
    il LED, che ne occupa 400 -- la stessa mediana li' sposterebbe la stima di
    4 conteggi, piu' del segnale.
    """
    base = np.median(dati, axis=2)
    corr = dati - base[:, :, None]
    if segni is not None:
        corr = corr * np.asarray(segni, dtype=float)[None, :, None]
    per_canale = corr.max(axis=2) * MV_PER_CONTEGGIO     # (eventi, canali)
    return per_canale.max(axis=1), per_canale


def marca_spuri(per_canale):
    """True dove tutti i canali hanno la stessa ampiezza entro la tolleranza."""
    if per_canale.shape[1] < 2:
        return np.zeros(per_canale.shape[0], dtype=bool)
    massimo = per_canale.max(axis=1)
    minimo = per_canale.min(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        rapporto = np.where(massimo > 0, minimo / massimo, 0.0)
    return rapporto > (1.0 - TOLLERANZA_SPURI)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scan", help="il JSON di noise_scan.py")
    ap.add_argument("-d", "--data-dir", default=os.path.join(ROOT, "data"))
    ap.add_argument("--riferimento", type=int, default=0,
                    help="indice del passo da cui prendere la distribuzione (default: il primo)")
    ap.add_argument("--tieni-spuri", action="store_true",
                    help="non vetare i picchi spuri del V1742")
    ap.add_argument("--verifica", action="store_true",
                    help="secondo stimatore indipendente: il bordo basso di ogni passo")
    ap.add_argument("-o", "--out", default=None, help="JSON con i dati grezzi")
    a = ap.parse_args()

    d = json.load(open(a.scan))
    punti = d["punti"]
    canali = [int(c) for c in d["canali"]]
    rif = punti[a.riferimento]

    h5 = os.path.join(a.data_dir, d["file"])
    if not os.path.exists(h5):
        for cand in (h5 + ".gz", h5.replace(".h5", "") + ".h5.gz"):
            if os.path.exists(cand):
                h5 = cand
                break
    if not os.path.exists(h5):
        sys.exit("non trovo il file della run: %s" % d["file"])

    print("scan        : %s" % os.path.basename(a.scan))
    print("run         : %s" % os.path.basename(h5))
    print("canali      : %s" % ", ".join(str(c) for c in canali))
    print("larghezza   : %s ns (FWHM dichiarata nello scan)" % d.get("larghezza_fwhm_ns"))
    print("riferimento : offset %g, eventi %d-%d, rate %.2f Hz"
          % (rif["offset"], rif["eventi_da"], rif["eventi_a"], rif["rate"]))

    # Si leggono solo gli eventi che servono: fino alla fine del passo di
    # riferimento. Su un file da 652 MB compressi fa la differenza fra un
    # minuto e dieci.
    t0 = time.time()
    fino_a = punti[-1]["eventi_a"] if a.verifica else rif["eventi_a"]
    hdr, dati = daqio.load(h5, max_events=fino_a, channels=canali,
                           live=h5.endswith(".h5"))
    print("letti       : %d eventi in %.0f s" % (len(dati), time.time() - t0))
    # Il verso di ogni canale, dalla stessa regola di monitor ed efficienza.
    import efficienza
    base_log = h5[:-3] if h5.endswith(".gz") else h5
    segni = efficienza._segni_per(canali, efficienza.polarita_dal_log(
        os.path.splitext(base_log)[0] + ".log"))
    print("verso       : %s" % ("  ".join("ch%d %s" % (c, "+" if v > 0 else "-")
                                           for c, v in zip(canali, segni))
                                if segni is not None else "non dichiarato, positivo"))

    blocco = dati[rif["eventi_da"]:rif["eventi_a"]]
    amp, per_canale = ampiezze_mV(blocco, segni)
    spuri = marca_spuri(per_canale)
    if not a.tieni_spuri:
        amp = amp[~spuri]
    print("spuri       : %d su %d (%.1f%%)%s"
          % (spuri.sum(), len(spuri), 100.0 * spuri.mean(),
             "" if not a.tieni_spuri else "  [tenuti]"))
    print("ampiezze    : mediana %.2f mV, minimo %.2f, massimo %.2f"
          % (np.median(amp), amp.min(), amp.max()))
    print()

    # --- la mappatura dei quantili ------------------------------------------
    r_rif = rif["rate"]
    righe = []
    for p in punti[a.riferimento:]:
        frazione = p["rate"] / r_rif
        if not (0.0 < frazione <= 1.0):
            continue                     # rate risalito: campione non stazionario
        soglia = float(np.quantile(amp, 1.0 - frazione))
        righe.append((p["offset"], p["rate"], frazione, soglia))

    print("%8s %10s %10s %12s %12s" % ("offset", "rate", "R/Rrif", "soglia mV", "mV/offset"))
    for off, rate, fr, s in righe:
        print("%8g %10.2f %10.4f %12.2f %12.4f"
              % (off, rate, fr, s, s / off if off else float("nan")))

    # --- il fit --------------------------------------------------------------
    # Si escludono i punti con frazione troppo vicina a 1: li' il quantile cade
    # sul bordo del campione e la soglia non e' determinata.
    buoni = [r for r in righe if r[2] < 0.98]
    if len(buoni) < 3:
        sys.exit("\ntroppi pochi punti utili per un fit")
    x = np.array([r[0] for r in buoni], float)
    y = np.array([r[3] for r in buoni], float)
    pend, inter = np.polyfit(x, y, 1)
    res = y - (pend * x + inter)
    sigma = float(np.sqrt(np.sum(res ** 2) / (len(x) - 2)))
    # errore sulla pendenza, dalla dispersione dei residui
    err_pend = sigma / np.sqrt(np.sum((x - x.mean()) ** 2))

    print()
    print("fit su %d punti (offset %g-%g):" % (len(x), x.min(), x.max()))
    print("  pendenza   : %.4f +- %.4f mV per unita' di offset" % (pend, err_pend))
    print("  intercetta : %.2f mV" % inter)
    print("  residui    : %.2f mV rms" % sigma)
    print("  attenuazione del Transparent Mode: %.2f" % (pend / MV_PER_CONTEGGIO))
    if pend < MV_PER_CONTEGGIO:
        print("  ATTENZIONE: sotto il pavimento di %.3f mV/conteggio."
              % MV_PER_CONTEGGIO)
        print("  Vorrebbe dire che il Transparent Mode AMPLIFICA: impossibile.")
        print("  Un'ipotesi e' sbagliata -- il passo del DAC, o i canali, o il file.")
    if abs(inter) > 3 * sigma:
        print("  NOTA: intercetta diversa da zero di %.1f sigma. La calibrazione"
              % (abs(inter) / max(sigma, 1e-9)))
        print("  a un punto solo, che passa per l'origine, qui non e' applicabile.")

    # --- secondo stimatore, indipendente dal primo -------------------------
    # Gli eventi registrati a un passo sono quelli che hanno superato la sua
    # soglia: il BORDO BASSO della loro distribuzione di ampiezze e' la soglia
    # stessa, letta direttamente in millivolt. Non usa i rate, non usa il passo
    # di riferimento, non assume che il campione sia stazionario. Se i due
    # metodi danno la stessa pendenza, la calibrazione non dipende da come la
    # si e' estratta; se divergono, uno dei due ha un'ipotesi rotta e il numero
    # non va scritto da nessuna parte.
    verifica = []
    if a.verifica:
        print()
        print("%8s %10s %12s %12s" % ("offset", "eventi", "minimo mV", "1% mV"))
        for p in punti:
            blk = dati[p["eventi_da"]:p["eventi_a"]]
            if len(blk) < 50:
                continue
            am, pc = ampiezze_mV(blk, segni)
            if not a.tieni_spuri:
                am = am[~marca_spuri(pc)]
            if len(am) < 50:
                continue
            q1 = float(np.quantile(am, 0.01))
            verifica.append((p["offset"], len(am), float(am.min()), q1))
            print("%8g %10d %12.2f %12.2f" % (p["offset"], len(am), am.min(), q1))
        if len(verifica) >= 3:
            xv = np.array([r[0] for r in verifica], float)
            yv = np.array([r[3] for r in verifica], float)
            pv, iv = np.polyfit(xv, yv, 1)
            rv = yv - (pv * xv + iv)
            sv = float(np.sqrt(np.sum(rv ** 2) / (len(xv) - 2)))
            ev = sv / np.sqrt(np.sum((xv - xv.mean()) ** 2))
            print()
            print("  secondo metodo (bordo basso, 1%% ): %.4f +- %.4f mV/offset,"
                  " intercetta %.2f mV" % (pv, ev, iv))
            diff = abs(pv - pend) / np.sqrt(ev ** 2 + err_pend ** 2)
            print("  differenza fra i due metodi: %.1f sigma" % diff)

    out = a.out or os.path.join(
        ROOT, "measurements",
        "calib_selftrigger_%s.json" % os.path.basename(a.scan)
        .replace("noise_scan_", "").replace(".json", ""))
    json.dump({
        "scan": os.path.basename(a.scan),
        "run": os.path.basename(h5),
        "canali": canali,
        "larghezza_fwhm_ns": d.get("larghezza_fwhm_ns"),
        "mv_per_conteggio": MV_PER_CONTEGGIO,
        "spuri_vetati": bool(not a.tieni_spuri),
        "frazione_spuri": float(spuri.mean()),
        "punti": [{"offset": o, "rate": r, "frazione": f, "soglia_mv": s}
                  for o, r, f, s in righe],
        "verifica_bordo_basso": [
            {"offset": o, "eventi": n_, "minimo_mv": mi, "q1_mv": q}
            for o, n_, mi, q in verifica],
        "fit": {"pendenza_mv_per_offset": pend, "errore_pendenza": float(err_pend),
                "intercetta_mv": inter, "residui_rms_mv": sigma,
                "attenuazione": pend / MV_PER_CONTEGGIO, "n_punti": len(x)},
    }, open(out, "w"), indent=2)
    print("\ndati grezzi in %s" % os.path.relpath(out, ROOT))


if __name__ == "__main__":
    main()
