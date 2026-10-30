"""Gli eventi con un solo PMT nello scan del self-trigger del 2026-10-30, con la
ricetta di carica delle run 0011 e 0013.

    python3 analisi/20261030_calibrazione_selftrigger/un_pmt_scan.py RUN.h5 SCAN_ch20.json SCAN_ch21.json SCAN_SIPM.json OUT

A ogni passo dello scan di un PMT gli altri canali stanno alla soglia massima:
ha sparato quel PMT, e l'altro e' registrato com'era. Per ogni evento la carica
di tutti e due, come in soglie_lngs.py: piedistallo = media dei campioni prima
di 150 ns, carica = somma fra 240 e 500 ns (impulsi a ~260 ns, come nella
0011 e nella 0013). Gli impulsi dei PMT sono positivi: dal 2026-10-30 passano
da un inverter.

La sigma della carica a vuoto di un PMT si prende da lui stesso quando e'
parcheggiato, negli eventi dello scan dell'altro PMT: stesse condizioni degli
eventi che si classificano, e niente SiPM accesi. Come controllo si stampa
anche quella del passo piu' basso dello scan di un SiPM (SCAN_SIPM.json), dove
i due PMT sono parcheggiati: sono risultate uguali entro il 5% (2026-10-30),
e piu' larghe del 25-50% che nella 0011 e nella 0013.

Scrive OUT.npz (cariche e ampiezze evento per evento, col canale e l'offset del
passo; durata di ogni passo) e OUT.json (rate per passo: totale, con un PMT
solo, con entrambi). Gira sul PC DAQ, dove sta la run.
"""
import json
import os
import sys

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import daqio                                           # noqa: E402

RUN, OUT = sys.argv[1], sys.argv[-1]
SCAN_PMT, SCAN_SIPM = sys.argv[2:4], sys.argv[4]
PMT = (20, 21)


def sig(x):
    """Come soglie_lngs.py: deviazione standard con esclusione a 3.5 sigma."""
    x = x[np.abs(x) < 1500]
    m, s_ = x.mean(), x.std()
    for _ in range(5):
        k = np.abs(x - m) < 3.5 * s_
        m, s_ = x[k].mean(), x[k].std()
    return float(s_)


passi = []
for f in SCAN_PMT:
    d = json.load(open(f))
    for p in d["punti"]:
        passi.append((int(d["canali"][0]), p))
ps = min(json.load(open(SCAN_SIPM))["punti"], key=lambda p: p["offset"])
fino_a = max([p["eventi_a"] for _, p in passi] + [ps["eventi_a"]])
hdr, w = daqio.load(RUN, max_events=fino_a, channels=list(PMT), live=False)
dt = float(hdr["SamplingTime"]) * 1e9
t = np.arange(w.shape[2]) * dt
g = (t >= 240) & (t < 500)


def cariche(blocco):
    y = blocco.astype(float)
    y = y - y[:, :, t < 150].mean(axis=2, keepdims=True)
    return y[:, :, g].sum(axis=2) * dt, y[:, :, g].max(axis=2)


qs, _ = cariche(w[ps["eventi_da"]:ps["eventi_a"]])
S_sipm = np.array([sig(qs[:, 0]), sig(qs[:, 1])])
parcheggiato = {0: [], 1: []}
for ch, p in passi:
    q, _ = cariche(w[p["eventi_da"]:p["eventi_a"]])
    parcheggiato[1 - PMT.index(ch)].append(q[:, 1 - PMT.index(ch)])
S = np.array([sig(np.concatenate(parcheggiato[0])), sig(np.concatenate(parcheggiato[1]))])
print("sigma della carica a vuoto, dal PMT parcheggiato: ch20 %.0f, ch21 %.0f ADC*ns "
      "(dal passo a offset %g del SiPM: %.0f, %.0f)" % (S[0], S[1], ps["offset"], S_sipm[0], S_sipm[1]))

Q, A, C, O = [], [], [], []
tabella, durate = [], {}
for ch, p in sorted(passi, key=lambda x: (x[0], -x[1]["offset"])):
    q, amp = cariche(w[p["eventi_da"]:p["eventi_a"]])
    n = len(q)
    durata = n / p["rate"] if p["rate"] > 0 else float("nan")
    j = PMT.index(ch)
    sopra = q > 3 * S[None, :]
    solo = sopra[:, j] & ~sopra[:, 1 - j]
    entrambi = sopra[:, 0] & sopra[:, 1]
    tabella.append({"canale": ch, "offset": p["offset"], "eventi": n, "durata_s": durata,
                    "rate_hz": p["rate"], "un_pmt_hz": solo.sum() / durata,
                    "entrambi_hz": entrambi.sum() / durata,
                    "nessuno_hz": (~sopra[:, j]).sum() / durata})
    durate["ch%d_off%g" % (ch, p["offset"])] = durata
    Q.append(q), A.append(amp), C.append(np.full(n, ch)), O.append(np.full(n, p["offset"]))
    r = tabella[-1]
    print("ch%d offset %5g: %5d eventi, %6.2f Hz; un PMT solo %6.3f Hz, entrambi %6.3f Hz, "
          "quel PMT sotto 3 sigma %6.3f Hz" % (ch, p["offset"], n, r["rate_hz"], r["un_pmt_hz"],
                                               r["entrambi_hz"], r["nessuno_hz"]))

np.savez_compressed(OUT + ".npz", q=np.concatenate(Q), amp=np.concatenate(A),
                    canale=np.concatenate(C), offset=np.concatenate(O), sigma_q=S,
                    passo=np.array(list(durate.keys())), durata=np.array(list(durate.values())))
json.dump({"run": os.path.basename(RUN), "sigma_q": S.tolist(), "sigma_q_passo_sipm": S_sipm.tolist(),
           "cancello_ns": [240, 500],
           "piedistallo_ns": [0, 150], "passi": tabella}, open(OUT + ".json", "w"), indent=2)
print("scritto", OUT + ".npz", OUT + ".json")
