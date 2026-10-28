#!/usr/bin/env python3
"""Distribuzioni di carica per canale di una run, con la correzione PADME per
cella del DRS4 presa dalla run stessa.

    python3 tools/carica_padme.py data/LNGS/LNGS__54-0V_0011_750Ms_55PT.h5.gz
    python3 tools/carica_padme.py RUN.h5.gz --canali 20 21 --cancello 20 590 640

Carica in ADC*ns: somma dei campioni meno il piedistallo dell'evento, per il
passo di campionamento, col segno del verso del canale (positiva anche per i
PMT negativi). 1 pC = 204.8 ADC*ns su 50 ohm e 1 Vpp.

PADME (lo stesso metodo di tools/padme.py e del test beam): ogni cella del
DRS4 ha un suo piedistallo, il campione k sta nella cella (k + SIC) mod 1024,
e la correzione toglie a ogni campione lo scostamento della sua cella.
Qui la tabella si prende dalla run stessa e NON da una run a trigger
software, per decisione dell'utente: le run a trigger software portano
raffiche agganciate al trigger (CLAUDE.md, 2026-10-26).

Perche' si puo' prendere dalla run stessa: il self-trigger e' asincrono
rispetto all'anello del DRS4, quindi la SIC gira su tutte le celle e ogni
cella si vede migliaia di volte. Dalla tabella si escludono i campioni dal
cancello in poi, perche' l'impulso cade sempre allo stesso indice di
lettura e, per i PMT amplificati, e' seguito da ~800 ns di coda di verso
opposto: la mediana regge gli impulsi rari, non uno spostamento sistematico
di meta' della traccia. Restano i campioni della finestra del piedistallo,
che e' piu' di un terzo della traccia.

Cancello e piedistallo sono quelli automatici del monitor (live_monitor.py,
_finestre_auto): forma d'onda media del 20% di eventi piu' alti, cancello da
10 ns prima a 20 ns dopo il tratto sopra il 10% del picco, piedistallo da
10 ns fino a 20 ns prima del cancello. Si misurano sui dati gia' corretti.

Accanto a ogni distribuzione c'e' quella "a vuoto": la stessa somma, con lo
stesso piedistallo, in una finestra larga come il cancello ma dentro il
tratto di solo piedistallo. La sua larghezza e' il contributo del rumore
alla carica, e il confronto con e senza PADME dice quanto la correzione
serve per la carica, che e' il numero che conta qui.
"""

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import efficienza   # noqa: E402
import padme        # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
N_CELLE = padme.N_CELLE
CANALI_PER_GRUPPO = padme.CANALI_PER_GRUPPO
INIZIO_PED_NS = 10.0
MIN_CAMPIONI_PED = 20


def finestre(y, segno, t_ns):
    """Cancello (ns) e piedistallo (indici) dalla forma d'onda media, come il
    monitor. None se il canale non ha impulsi riconoscibili."""
    x = segno * (y - np.median(y, axis=1, keepdims=True))
    amp = x.max(axis=1)
    sel = amp >= np.percentile(amp, 80)
    m = x[sel].mean(axis=0)
    p = int(np.argmax(m))
    picco = float(m[p])
    rumore = float(np.median(np.std(x[:, :max(MIN_CAMPIONI_PED, p // 2)], axis=1)))
    if picco < 5 * max(rumore, 0.5):
        return None
    a = p
    while a > 0 and m[a] > 0.1 * picco:
        a -= 1
    b = p
    while b < len(m) - 1 and m[b] > 0.1 * picco:
        b += 1
    return (max(0.0, float(t_ns[a]) - 10.0), min(float(t_ns[-1]), float(t_ns[b]) + 20.0))


def indici_ped(t_ns, cancello):
    i0 = int(np.searchsorted(t_ns, INIZIO_PED_NS))
    i1 = int(np.searchsorted(t_ns, cancello[0] - 20.0))
    return i0, i1


def tabella_padme(y, sic, i0, i1):
    """Scostamento per cella, in conteggi, dai soli campioni [i0, i1)."""
    k = np.arange(i0, i1)
    cella = (k[None, :] + sic[:, None]) % N_CELLE
    tab, conta = padme.mediana_per_gruppo(cella, y[:, i0:i1], N_CELLE)
    tab -= np.nanmedian(tab)
    return np.nan_to_num(tab), conta


def cariche(y, dt, segno, gi, pi, vi):
    """Carica nel cancello e a vuoto, ADC*ns, col piedistallo per evento."""
    ped = y[:, pi[0]:pi[1]].mean(axis=1, keepdims=True)
    z = segno * (y - ped)
    return z[:, gi[0]:gi[1]].sum(axis=1) * dt, z[:, vi[0]:vi[1]].sum(axis=1) * dt


def sigma_centrale(q):
    """Larghezza del picco a vuoto: deviazione standard con esclusione a 3.5
    sigma, per non farla gonfiare dai pochi eventi con un impulso dentro."""
    return padme.sigma_robusta(q)


def grafico(ris, titolo, path, t_ns):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    canali = list(ris)
    fig, assi = plt.subplots(len(canali), 2, figsize=(14, 3.3 * len(canali)),
                             squeeze=False, width_ratios=[1, 1.5])
    for riga, ch in zip(assi, canali):
        r = ris[ch]
        a = riga[0]
        a.plot(t_ns, r["media_padme"], lw=0.8, color="#2a6fdb")
        a.axvspan(*r["cancello_ns"], color="#e8710a", alpha=0.18, label="gate")
        a.axvspan(t_ns[r["ped"][0]], t_ns[r["ped"][1] - 1], color="#888", alpha=0.18,
                  label="pedestal")
        a.axvspan(t_ns[r["vuoto"][0]], t_ns[r["vuoto"][1] - 1], fill=False, hatch="//",
                  edgecolor="#888", lw=0, label="blank gate")
        a.set_title("ch%d  mean waveform, 20%% highest events  (%s)"
                    % (ch, "positive" if r["segno"] > 0 else "negative"), fontsize=9)
        a.set_xlabel("time [ns]")
        a.set_ylabel("pulse, sign-corrected [counts]")
        a.legend(fontsize=7, loc="upper right")
        a.grid(alpha=0.25)

        a = riga[1]
        tutte = np.concatenate([r["q_caen"], r["q_padme"], r["v_caen"], r["v_padme"]])
        lo, hi = np.percentile(tutte, [0.2, 99.8])
        lo -= 0.05 * (hi - lo)
        hi += 0.05 * (hi - lo)
        bins = np.linspace(lo, hi, 200)
        a.hist(r["v_caen"], bins, histtype="step", color="#aaa", lw=1,
               label="blank %.0f ns, CAEN only  σ = %.0f" % (r["vuoto_ns"], r["sigma_vuoto_caen"]))
        a.hist(r["v_padme"], bins, histtype="step", color="#555", lw=1,
               label="blank %.0f ns, + PADME  σ = %.0f" % (r["vuoto_ns"], r["sigma_vuoto_padme"]))
        a.hist(r["q_caen"], bins, histtype="step", color="#e8710a", lw=1.2,
               label="gate, CAEN only")
        a.hist(r["q_padme"], bins, histtype="step", color="#2a6fdb", lw=1.4,
               label="gate, + PADME")
        a.set_yscale("log")
        a.set_xlabel("charge [ADC·ns]   (1 pC = 204.8 ADC·ns)")
        a.set_ylabel("events / bin")
        a.set_title("ch%d  charge in %.0f-%.0f ns, %d events, %d saturated  "
                    "(pedestal rms %.2f → %.2f counts)"
                    % (ch, r["cancello_ns"][0], r["cancello_ns"][1], len(r["q_padme"]),
                       r["saturati"], r["rms_ped_caen"], r["rms_ped_padme"]), fontsize=9)
        a.legend(fontsize=7.5, frameon=False)
        a.grid(alpha=0.25, which="both")
    fig.suptitle(titolo, fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--canali", type=int, nargs="+", help="di serie tutti quelli del file")
    ap.add_argument("-n", "--max-eventi", type=int, default=0, help="0 = tutti (di serie)")
    ap.add_argument("--cancello", type=float, nargs=3, action="append", default=[],
                    metavar=("CH", "DA_NS", "A_NS"),
                    help="cancello scritto a mano per un canale; ripetibile")
    ap.add_argument("--out", default=None,
                    help="prefisso dei file di uscita (di serie measurements/carica_<run>)")
    a = ap.parse_args()

    hdr, canali, dati, sic = padme.leggi(a.file, a.canali, a.max_eventi or None)
    run = efficienza.nome_run(a.file)
    ns = dati.shape[2]
    dt = 1000.0 / float(str(hdr.get("SamplingRate", "750")).rstrip("MHzmhz") or 750)
    t_ns = np.arange(ns) * dt
    log = os.path.splitext(a.file[:-3] if a.file.endswith(".gz") else a.file)[0] + ".log"
    versi = efficienza.polarita_dal_log(log)
    a_mano = {int(c): (da, al) for c, da, al in a.cancello}
    print("%s: %d eventi, %d campioni a %.3f ns, correzione CAEN %s"
          % (run, dati.shape[0], ns, dt,
             {True: "ACCESA", False: "spenta", None: "sconosciuta"}[padme.correzione_caen(a.file)]))

    ris = {}
    for j, ch in enumerate(canali):
        segno = versi.get(ch, 1) if isinstance(versi, dict) else (versi or 1)
        g = ch // CANALI_PER_GRUPPO
        s = sic[:, g]
        if np.any(s > N_CELLE - 1):
            print("ch%d: gruppo %d senza SIC, salto" % (ch, g))
            continue
        y = dati[:, j, :].astype(np.float32)

        # Primo giro sui dati grezzi solo per sapere dove sta l'impulso, e
        # quindi quali campioni possono entrare nella tabella.
        canc = a_mano.get(ch) or finestre(y, segno, t_ns)
        if canc is None:
            print("ch%d: nessun impulso riconoscibile nella forma d'onda media; "
                  "dare il cancello con --cancello %d DA A" % (ch, ch))
            continue
        pi = indici_ped(t_ns, canc)
        if pi[1] - pi[0] < MIN_CAMPIONI_PED:
            print("ch%d: cancello a %.0f ns, troppo presto per un piedistallo prima" % (ch, canc[0]))
            continue
        tab, conta = tabella_padme(y, s, *pi)
        celle = (np.arange(ns)[None, :] + s[:, None]) % N_CELLE
        yp = y - tab[celle]

        # Secondo giro sui dati corretti: e' su quelli che si fa la carica.
        if ch not in a_mano:
            canc = finestre(yp, segno, t_ns) or canc
            pi = indici_ped(t_ns, canc)
        gi = (int(np.searchsorted(t_ns, canc[0])), int(np.searchsorted(t_ns, canc[1])))
        # A vuoto: la finestra all'inizio del tratto di piedistallo, il
        # piedistallo da quello che resta dopo. Separati, come nella carica
        # vera, se no la somma a vuoto e' zero per costruzione. Quando il
        # cancello e' piu' lungo di meta' del piedistallo (SiPM) la finestra a
        # vuoto e' piu' corta del cancello, e il grafico lo scrive.
        larg = min(gi[1] - gi[0], (pi[1] - pi[0]) // 2)
        vi = (pi[0], pi[0] + larg)
        pv = (pi[0] + larg, pi[1])
        q_c, _ = cariche(y, dt, segno, gi, pi, vi)
        q_p, _ = cariche(yp, dt, segno, gi, pi, vi)
        _, v_c = cariche(y, dt, segno, gi, pv, vi)
        _, v_p = cariche(yp, dt, segno, gi, pv, vi)
        # Un PMT negativo satura a 0 conteggi, un SiPM positivo a 4095: oltre,
        # la carica e' un limite inferiore.
        sat = np.any((y[:, gi[0]:gi[1]] <= 0) | (y[:, gi[0]:gi[1]] >= 4095), axis=1)
        ped_c = y[:, pi[0]:pi[1]]
        ped_p = yp[:, pi[0]:pi[1]]
        r = {
            "segno": segno, "gruppo": g,
            "cancello_ns": canc, "ped": pi, "vuoto": vi,
            "celle_viste": int((conta > 0).sum()), "campioni_per_cella_min": int(conta.min()),
            "dispersione_fra_celle": float(np.std(tab)),
            "rms_ped_caen": float(np.median(np.std(ped_c, axis=1))),
            "rms_ped_padme": float(np.median(np.std(ped_p, axis=1))),
            "sigma_vuoto_caen": sigma_centrale(v_c),
            "sigma_vuoto_padme": sigma_centrale(v_p),
            "saturati": int(sat.sum()), "vuoto_ns": float(larg * dt),
            "q_caen": q_c, "q_padme": q_p, "v_caen": v_c, "v_padme": v_p,
            "media_padme": (segno * (yp - yp[:, pi[0]:pi[1]].mean(axis=1, keepdims=True))
                            )[np.argsort(q_p)[int(0.8 * len(q_p)):]].mean(axis=0),
            "tabella": tab,
        }
        ris[ch] = r
        print("ch%d (%s): cancello %.0f-%.0f ns, piedistallo %.0f-%.0f ns; celle %d, >= %d "
              "campioni l'una, dispersione fra celle %.2f conteggi\n"
              "      rms piedistallo %.2f -> %.2f conteggi; sigma a vuoto %.0f -> %.0f ADC*ns; "
              "carica mediana %.0f ADC*ns, 99%% sotto %.0f; saturi %d"
              % (ch, "+" if segno > 0 else "-", canc[0], canc[1], t_ns[pi[0]], t_ns[pi[1] - 1],
                 r["celle_viste"], r["campioni_per_cella_min"], r["dispersione_fra_celle"],
                 r["rms_ped_caen"], r["rms_ped_padme"], r["sigma_vuoto_caen"],
                 r["sigma_vuoto_padme"], np.median(q_p), np.percentile(q_p, 99), r["saturati"]))

    if not ris:
        sys.exit("nessun canale analizzabile")
    pref = a.out or os.path.join(ROOT, "measurements", "carica_%s" % run)
    os.makedirs(os.path.dirname(os.path.abspath(pref)), exist_ok=True)
    # Le cariche evento per evento, per i passi successivi (coincidenze fra
    # canali, tagli): si rileggono da qui senza rifare la correzione.
    np.savez_compressed(pref + ".npz", canali=np.array(list(ris)),
                        **{"q_padme_ch%d" % c: r["q_padme"] for c, r in ris.items()},
                        **{"q_caen_ch%d" % c: r["q_caen"] for c, r in ris.items()},
                        **{"vuoto_padme_ch%d" % c: r["v_padme"] for c, r in ris.items()},
                        **{"tabella_padme_ch%d" % c: r["tabella"] for c, r in ris.items()})
    with open(pref + ".json", "w") as f:
        json.dump({"file": os.path.abspath(a.file), "run": run, "dt_ns": dt,
                   "unita": "ADC*ns (1 pC = 204.8)",
                   "canali": {str(c): {k: (list(v) if isinstance(v, tuple) else v)
                                       for k, v in r.items() if not isinstance(v, np.ndarray)}
                              for c, r in ris.items()}}, f, indent=1)
    grafico(ris, "%s — charge per channel, PADME per-cell correction from the run itself" % run,
            pref + ".png", t_ns)
    print("scritti %s.png, .json, .npz" % pref)


if __name__ == "__main__":
    main()
