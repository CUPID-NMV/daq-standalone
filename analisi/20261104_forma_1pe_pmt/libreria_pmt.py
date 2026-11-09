"""Libreria dei PMT del WC con la catena di ADESSO (scheda di amplificazione e
inverter, dal 2026-10-30), dallo scan del self-trigger del 2026-10-30: per
`digit_mc.py --libreria`, al posto di libreria_digit_0013.npz. Stesse chiavi e
stesse convenzioni di 20261030_fondo_0013/libreria_digit.py, cosi' digit_mc non
cambia.

    python3 analisi/20261104_forma_1pe_pmt/libreria_pmt.py RUN SCAN_ch20.json SCAN_ch21.json

RUN e' la run dello scan (LNGS_scan_54-0V_0000_750Ms_55PT.h5.gz), oppure un .npz
con `d` (eventi x [ch20, ch21] x campioni, i primi 40000 eventi della run) e
`dt_ns`: dal bucket la run si legge in ~10 minuti. Scrive
measurements/libreria_digit_pmt_20261109.npz e un PNG accanto.

Baco corretto il 2026-11-09 (solo ch21). Fino ad allora gli eventi senza impulso
di tutti e due i PMT venivano dallo scan dei SiPM, e nei 40000 eventi letti c'e'
solo lo scan del ch16: li' il ch21 porta la diafonia del ch16 (+1.6 conteggi a
240-340 ns, +1.1 a 340-500). Tolta dagli impulsi del ch21, che non ce l'hanno,
diventava un'undershoot finta: coda della forma dimezzata (a +40 ns 0.010 del
picco contro 0.021 negli impulsi veri), PE sottostimato del 4%, +0.07 PE nel
cancello in ogni traccia di rumore. measurements/libreria_digit_pmt_20261030.npz
e' quella col baco, tenuta per le produzioni gia' fatte. Diagnosi:
~/Testbeam2026_WC_pannelli/data/produzione_hallA/daq_24h/confronto_20261109/baco_forma_ch21.ipynb.

  forma_chXX    forma del SINGOLO fotoelettrone (la 0013 aveva lampi di qualche
                PE): impulsi dei passi piu' bassi dello scan di quel PMT
                (offset 10-20, gli altri canali parcheggiati: ~25 Hz di impulsi
                propri del PMT, quasi tutti da un fotoelettrone), ampiezza nella
                gobba del PE (15-75 mV), niente altro nella traccia. Prima di
                allinearli si toglie la traccia media degli eventi senza
                impulso dello stesso canale (VUOTI_DA: ch20 dallo scan dei
                SiPM, ch21 dallo scan del ch20, senza la diafonia del ch16):
                l'oscillazione dei primi 100 ns e
                la struttura del DRS4 cadono allo stesso istante in tutti gli
                eventi, e l'impulso del self-trigger anche. Poi allineamento al
                50% del fronte, passo fine (gli impulsi cadono a fase casuale
                rispetto al campionamento), su tutta la finestra dopo
                l'impulso: la coda, se c'e', c'e' tutta. Area K_chXX / K invece
                che 1 (digit_mc ha un K solo per i due PMT, e con l'inverter le
                cariche per PE dei due canali sono diverse).
  rumore_chXX   tracce grezze dello stesso canale senza impulsi, dagli stessi
                eventi vuoti (VUOTI_DA: ch20 dallo scan dei SiPM, PMT
                parcheggiati; ch21 dallo scan del ch20, SiPM parcheggiati,
                perche' nello scan del ch16 porta la sua diafonia), SCRITTE COME 4095 -
                traccia: digit_mc sottrae l'impulso (convenzione della 0013,
                impulsi negativi) e taglia a 0, che cosi' e' il fondo scala vero
                degli impulsi positivi di adesso.
  t50_chXX      istante del 50% del fronte nella finestra (self-trigger, 55%).
  sogliaA_chXX  bordo del trigger a offset 100, come per la 0013: il 2
                percentile dell'ampiezza (massimo in 240-500 ns sopra il
                piedistallo 0-150 ns) degli impulsi del passo a offset 100.
  K_adcns_per_pe, K_chXX  area di un fotoelettrone in ADC*ns: gobba del PE
                nella carica in -10..+30 ns attorno al picco al passo a offset
                10 (come spe_pmt_scan.py), divisa per la frazione dell'area
                della forma che cade in quella finestra. digit_mc la usa se non
                gli si da' --k.
"""
# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os, sys as _sys
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
_sys.path.insert(0, _os.path.join(ROOT, "tools"))

import json
import sys
import numpy as np
from scipy.optimize import curve_fit

CANALI = [20, 21]
N_EVENTI = 40000                 # fino a un tratto dello scan dei SiPM, per il rumore
FINE = 0.1
PED_NS, GATE_NS = 150.0, (240.0, 500.0)
AMP_PE_MV = (15.0, 75.0)
OFFSET_PE = (10.0, 15.0, 20.0)
N_RUMORE = 1500
MV = 1000.0 / 4096.0
Q_FIT = [(600, 3600, 150.0), (900, 3300, 150.0), (450, 4000, 150.0)]
# Da dove si prendono gli eventi senza impulso di ogni PMT (traccia media da togliere agli
# impulsi, tracce di rumore): "sipm" = lo scan dei SiPM, PMT parcheggiati; un numero = lo scan
# di quel PMT, SiPM parcheggiati. Il ch21 non dallo scan dei SiPM: nei 40000 eventi letti c'e'
# solo quello del ch16, che nel ch21 lascia la sua diafonia (+1.6 conteggi a 240-500 ns).
VUOTI_DA = {20: "sipm", 21: 20}
OUT = _os.path.join(ROOT, "measurements", "libreria_digit_pmt_20261109.npz")
VECCHIA = _os.path.join(ROOT, "measurements", "libreria_digit_0013.npz")


def gauss(x, n, mu, sg):
    return n * np.exp(-0.5 * ((x - mu) / sg) ** 2)


def gobba(v, intervalli):
    """Media della gobba del PE su piu' intervalli: (mu, sigma, dispersione di mu)."""
    mus, sgs = [], []
    for lo, hi, passo in intervalli:
        h, b = np.histogram(v, np.arange(lo, hi + passo, passo))
        c = 0.5 * (b[1:] + b[:-1])
        p, _ = curve_fit(gauss, c, h, p0=[h.max(), c[h.argmax()], (hi - lo) / 4.0],
                         sigma=np.sqrt(np.maximum(h, 1)), absolute_sigma=True)
        mus.append(float(p[1])); sgs.append(abs(float(p[2])))
    return mus[0], sgs[0], max(mus) - min(mus)


def eventi(scan, offsets):
    return np.concatenate([np.arange(p["eventi_da"], p["eventi_a"]) for p in scan["punti"] if p["offset"] in offsets])


def costruisci(d, dt, scans):
    """d: eventi x [ch20, ch21] x campioni (conteggi grezzi, impulsi positivi)."""
    T = np.arange(d.shape[2]) * dt
    ped_m, gate = T < PED_NS, (T >= GATE_NS[0]) & (T < GATE_NS[1])
    dentro = (T > 20) & (T < T[-1] - 30)          # in fondo il V1742 mette spesso un picco spurio
    inizio_sipm = min(p["eventi_da"] for s in scans["sipm"] for p in s["punti"])
    out = {"dt_ns": dt, "fine_ns": FINE, "canali": np.array(CANALI)}
    ris = {}
    for j, ch in enumerate(CANALI):
        sc = scans[ch]
        raw = d[:, j, :].astype(float)
        y = raw - raw[:, ped_m].mean(1, keepdims=True)
        q = y[:, gate].sum(1) * dt
        # eventi senza impulso: PMT parcheggiato durante lo scan dei SiPM, o (VUOTI_DA)
        # durante lo scan dell'altro PMT
        if VUOTI_DA[ch] == "sipm":
            park = np.arange(inizio_sipm, len(d))
        else:
            sc_vuoti = scans[VUOTI_DA[ch]]
            park = eventi(sc_vuoti, [p["offset"] for p in sc_vuoti["punti"]])
            park = park[park < len(d)]
        s_raw = 1.4826 * np.median(np.abs(y[park][:, dentro]))
        s_q = 1.4826 * np.median(np.abs(q[park] - np.median(q[park])))
        vuoti = park[(np.abs(y[park][:, dentro]).max(1) < 6 * s_raw) & (np.abs(q[park] - np.median(q[park])) < 3 * s_q)]
        Y0 = y[vuoti].mean(0)
        rng = np.random.default_rng(j)
        scelti = rng.choice(vuoti, min(N_RUMORE, len(vuoti)), replace=False)
        out["rumore_ch%d" % ch] = (4095 - d[scelti, j, :].astype(np.int32)).astype(np.int16)
        # impulsi da un fotoelettrone
        ev = eventi(sc, OFFSET_PE)
        yy = y[ev] - Y0
        moda = int(np.bincount(yy.argmax(1)).argmax())
        lo, hi = max(moda - int(50 / dt), 0), min(moda + int(50 / dt), len(T))
        ip = lo + yy[:, lo:hi].argmax(1)
        A = yy[np.arange(len(ev)), ip]
        t50 = np.full(len(ev), np.nan)
        for k in range(len(ev)):
            i = ip[k]
            while i > 0 and yy[k, i - 1] >= 0.5 * A[k]:
                i -= 1
            if i > 0:
                t50[k] = T[i - 1] + (0.5 * A[k] - yy[k, i - 1]) / (yy[k, i] - yy[k, i - 1]) * dt
        fuori = dentro[None, :] & ((T[None, :] < t50[:, None] - 30) | (T[None, :] > t50[:, None] + 200))
        altro = np.where(fuori, np.abs(yy), 0).max(1)
        sel = (A * MV >= AMP_PE_MV[0]) & (A * MV <= AMP_PE_MV[1]) & np.isfinite(t50) & (altro < 10 / MV)
        t50m = float(np.median(t50[sel]))
        xf = np.arange(-30, T[-1] - t50m - 10, FINE)
        acc, n = np.zeros(len(xf)), np.zeros(len(xf))
        for k in np.flatnonzero(sel):
            v = np.interp(xf + t50[k], T, yy[k], left=np.nan, right=np.nan)
            ok = np.isfinite(v)
            acc[ok] += v[ok]; n[ok] += 1
        forma = acc / np.maximum(n, 1)
        forma -= forma[xf < -15].mean()
        forma /= forma.sum() * FINE                   # area 1, in 1/ns
        # K: gobba del PE nella carica in -10..+30 ns attorno al picco (passo a
        # offset 10), divisa per la parte della forma che cade in quella
        # finestra. Nel cancello lungo 240-500 la gobba si allarga col rumore
        # del piedistallo e il fit non e' stabile (i due canali venivano 1953 e
        # 1528 ADC*ns, mentre evento per evento differiscono del 3%).
        ev10 = eventi(sc, (10.0,))
        y10 = y[ev10] - Y0
        i10 = lo + y10[:, lo:hi].argmax(1)
        a0, a1 = int(round(10 / dt)), int(round(30 / dt))
        qn = np.array([y10[k, i - a0:i + a1 + 1].sum() * dt for k, i in enumerate(i10)])
        mu_q, sg_q, var_q = gobba(qn, Q_FIT)
        xp = xf[np.argmax(forma)]
        f_n = forma[(xf >= xp - 10) & (xf <= xp + 30)].sum() * FINE
        f_gate = forma[(xf >= GATE_NS[0] - t50m) & (xf < GATE_NS[1] - t50m)].sum() * FINE
        K = mu_q / f_n
        q10 = y10[:, gate].sum(1) * dt
        # bordo del trigger a offset 100
        ev100 = eventi(sc, (100.0,))
        sogliaA = float(np.percentile(y[ev100][:, gate].max(1), 2))
        out.update({"forma_ch%d" % ch: forma, "t50_ch%d" % ch: t50m, "sogliaA_ch%d" % ch: sogliaA,
                    "K_ch%d" % ch: K, "n_impulsi_ch%d" % ch: int(sel.sum()), "n_rumore_ch%d" % ch: len(scelti),
                    "sigma_rumore_ch%d" % ch: s_raw, "sigma_q_ch%d" % ch: s_q, "frazione_nel_cancello_ch%d" % ch: f_gate})
        ris[ch] = dict(xf=xf, forma=forma, A=A[sel] * MV, q10=q10, qn=qn, mu_q=mu_q, sg_q=sg_q, var_q=var_q, K=K,
                       sogliaA=sogliaA, t50=t50m, s_raw=s_raw, s_q=s_q, n=int(sel.sum()), n_ev=len(ev), f_gate=f_gate,
                       f_n=f_n)
    # una griglia comune (digit_mc ne usa una sola), e un K solo: la forma di
    # ciascun canale ha area K_ch / K invece di 1, cosi' il K unico di digit_mc
    # da' a ogni PMT la sua carica per PE
    x0 = max(ris[c]["xf"][0] for c in CANALI); x1 = min(ris[c]["xf"][-1] for c in CANALI)
    xf = np.arange(x0, x1, FINE)
    K = float(np.mean([ris[c]["K"] for c in CANALI]))
    for ch in CANALI:
        out["forma_ch%d" % ch] = np.interp(xf, ris[ch]["xf"], ris[ch]["forma"]) * ris[ch]["K"] / K
    out["forma_x_ns"] = xf
    out["K_adcns_per_pe"] = K
    return out, ris


def main():
    RUN, S20, S21 = sys.argv[1:4]
    scans = {20: json.load(open(S20)), 21: json.load(open(S21))}
    cart = _os.path.dirname(_os.path.abspath(S20))
    scans["sipm"] = [json.load(open(_os.path.join(cart, f))) for f in sorted(_os.listdir(cart))
                     if f.startswith("noise_scan_20261030_") and f.endswith((".json",)) and "_ch1" in f]
    if RUN.endswith(".npz"):
        z = np.load(RUN)
        d, dt = z["d"], float(z["dt_ns"])
    else:
        import padme
        h, c, d, _ = padme.leggi(RUN, CANALI, N_EVENTI, serve_sic=False)
        dt = float(h["SamplingTime"]) * 1e9
    out, ris = costruisci(d, dt, scans)
    out["run"] = _os.path.basename(RUN)
    vec = np.load(VECCHIA)
    for ch in CANALI:
        r = ris[ch]
        f, x = out["forma_ch%d" % ch], out["forma_x_ns"]
        pk = f.max(); ip = f.argmax()
        so = x[f >= 0.5 * pk]
        fo, xo = vec["forma_ch%d" % ch], vec["forma_x_ns"]
        soo = xo[fo >= 0.5 * fo.max()]
        v = lambda q: f[np.searchsorted(x, x[ip] + q)] / pk
        coda = f[x > x[ip] + 50].sum() * FINE
        print("ch%d: %d impulsi da 1 PE su %d (offset 10-20); 1 PE = %.1f mV di ampiezza (mediana)"
              % (ch, r["n"], r["n_ev"], np.median(r["A"])))
        print("   forma: FWHM %.1f ns (0013: %.1f); dal picco +20/+50/+100/+300/+600 ns %+.4f/%+.4f/%+.4f/%+.4f/%+.4f; "
              "area oltre +50 ns %.3f, minimo dopo il picco %+.4f del picco"
              % (so[-1] - so[0], soo[-1] - soo[0], v(20), v(50), v(100), v(300), v(600), coda,
                 f[x > x[ip]].min() / pk))
        print("   carica del PE in -10..+30 ns: %.0f ADC*ns (sigma %.0f, fra gli intervalli %.0f), %.3f della forma -> "
              "K = %.0f ADC*ns per PE; nel cancello 240-500 cade %.3f della forma, cioe' %.0f ADC*ns per PE"
              % (r["mu_q"], r["sg_q"], r["var_q"], r["f_n"], r["K"], r["f_gate"], r["K"] * r["f_gate"]))
        print("   t50 %.1f ns (0013: %.1f); bordo del trigger a offset 100: %.0f conteggi (0013: %.0f); rumore: %d tracce, "
              "sigma %.2f conteggi, carica a vuoto sigma %.0f ADC*ns"
              % (r["t50"], float(vec["t50_ch%d" % ch]), r["sogliaA"], float(vec["sogliaA_ch%d" % ch]),
                 out["n_rumore_ch%d" % ch], r["s_raw"], r["s_q"]))
    print("K medio %.0f ADC*ns per PE (mc_digit_K.json: 1647, dai muoni)" % out["K_adcns_per_pe"])
    np.savez_compressed(OUT, **out)
    print("scritto", OUT)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    COL = {20: "#2a78d6", 21: "#eb6834"}
    fig, ax = plt.subplots(1, 3, figsize=(18, 4.6))
    for ch in CANALI:
        f, x = out["forma_ch%d" % ch], out["forma_x_ns"]
        fo, xo = vec["forma_ch%d" % ch], vec["forma_x_ns"]
        ip, io = f.argmax(), fo.argmax()
        ax[0].plot(x - x[ip], f / f.max(), color=COL[ch], lw=1.3, label="ch%d, 1 PE, con l'inverter" % ch)
        ax[0].plot(xo - xo[io], fo / fo.max(), color=COL[ch], lw=1.0, ls="--", label="ch%d, 0013 (lampi)" % ch)
        ax[1].plot(x - x[ip], f / f.max(), color=COL[ch], lw=0.8, label="ch%d" % ch)
        r = ris[ch]
        h, b = np.histogram(r["qn"], np.arange(-1000, 8001, 150.0))
        ax[2].stairs(h, b, color=COL[ch], lw=1.3, label="ch%d: PE %.0f ADC*ns in -10..+30 ns" % (ch, r["mu_q"]))
    ax[0].set(xlabel="ns dal picco", ylabel="frazione del picco", xlim=(-20, 60), title="Forma dell'impulso")
    ax[1].set(xlabel="ns dal picco", ylabel="frazione del picco", ylim=(-0.03, 0.06), title="La coda (zoom)")
    ax[2].set(xlabel="carica in -10..+30 ns attorno al picco (ADC*ns)", ylabel="eventi (offset 10)",
              title="Carica del fotoelettrone")
    for a_ in ax:
        a_.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT.replace(".npz", ".png"), dpi=120)
    print("scritto", OUT.replace(".npz", ".png"))


if __name__ == "__main__":
    main()
