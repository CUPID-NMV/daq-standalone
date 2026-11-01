"""Rate atteso del veto completo con una soglia di ~1.8 PE su tutti i PMT, estrapolato
dal rate misurato col prototipo al LNGS e dall'area d'acqua del veto vero nel CAD.

    python3 analisi/20261101_rate_veto/rate_veto.py [--cad DIR] [--out PREFISSO]

Rate del prototipo: run 0013 (offset 100, la soglia che lo scan del 30/10 mette a
1.8-2.2 PE) e, per confronto, 0011 (offset 200, 3.5-4.3 PE). E' il rate di trigger: un
evento ogni volta che almeno uno dei due PMT supera la soglia, come nel veto che
acquisisce quando un PMT qualunque scatta.

Area: la mesh dei contenitori ricavata dal CAD 20260713-Assy-WC-Y.stp, quella che
disegna Analysis_script_background/view_cad_real.ipynb nel progetto MC (stessi file,
stessi colori per faccia). Per ogni faccia del veto si proiettano sul suo piano i
triangoli della superficie interna dei contenitori, cioe' il soffitto della cavita',
che e' la faccia dell'acqua: i fori veri ci sono gia'. Le pareti dei contenitori
(10 mm) restano fuori, come nell'area del prototipo, 960 x 360 mm d'acqua.
Non si usa cad/water_sectors_real.csv: le scatole dei suoi settori si sovrappongono sul
tetto e coprono il foro del fondo, e la loro somma fa 41 m^2 contro i ~32 della mesh.

Due estrapolazioni. (1) Tutto in proporzione all'area. (2) Gli eventi di un PMT solo
che il MC non spiega -- gli impulsi propri dei PMT, CLAUDE.md -- in proporzione al
numero di PMT, il resto (la luce nell'acqua e nelle fibre) all'area. Il veto vero ha
tank in media due volte piu' grandi del prototipo, quindi le due strade divergono.
"""
# --- percorsi: lo script gira da qualunque cartella, sul PC DAQ o sul Mac ---
import os as _os
ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))

import argparse
import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from PIL import Image, ImageDraw

ap = argparse.ArgumentParser()
ap.add_argument("--cad", default="/Users/benussi/Testbeam2026_WC_unified/cad",
                help="cartella con newcad_mesh.npz e pmt_centers.npy (progetto MC)")
ap.add_argument("--out", default=_os.path.join(ROOT, "measurements", "rate_veto_20261101"))
arg = ap.parse_args()
MIS = _os.path.join(ROOT, "measurements")

# ------------------------------------------------------------------ area d'acqua dal CAD
AREA_PROTO = 0.960 * 0.360                       # m^2, faccia dell'acqua del prototipo (geometria `single` del MC)
FACCE = ("Xp", "Xm", "Yp", "Ym", "Zp", "Zm")
COL = {"Xp": "#d62728", "Xm": "#ff9896", "Yp": "#1f77b4", "Ym": "#aec7e8", "Zp": "#2ca02c", "Zm": "#98df8a"}
LAB = {"Xp": "X+", "Xm": "X−", "Yp": "Y+", "Ym": "Y−", "Zp": "Z+ (roof)", "Zm": "Z− (floor)"}
ASSE = {"X": 0, "Y": 1, "Z": 2}

z = np.load(_os.path.join(arg.cad, "newcad_mesh.npz"))
V, F, C = z["V"], z["F"], z["C"]
PMT = np.load(_os.path.join(arg.cad, "pmt_centers.npy"))
T3 = V[F]
nrm = np.cross(T3[:, 1] - T3[:, 0], T3[:, 2] - T3[:, 0])
nrm /= np.clip(np.linalg.norm(nrm, axis=1), 1e-12, None)[:, None]


def area_unione(T2, passo=0.5):
    """Area dell'unione di triangoli 2D [mm], rasterizzati a `passo` mm: i triangoli di
    una superficie tassellata si toccano lungo i lati, e sommarne le aree conterebbe due
    volte quelli che si sovrappongono fra contenitori o fra le due pelli."""
    lo = T2.reshape(-1, 2).min(0) - 3
    W, H = np.ceil((T2.reshape(-1, 2).max(0) + 3 - lo) / passo).astype(int)
    im = Image.new("1", (W, H), 0)
    d = ImageDraw.Draw(im)
    for t in (T2 - lo) / passo:
        d.polygon([tuple(p) for p in t], fill=1)
    return np.asarray(im).sum() * passo ** 2 / 1e6


area, tri = {}, {}
for f in FACCE:
    ax = ASSE[f[0]]
    piano = [i for i in range(3) if i != ax]
    m = (C == f) & (np.abs(nrm[:, ax]) > 0.99)
    q = np.round(T3[:, :, ax].mean(1))
    # le pelli esterne del guscio stanno agli estremi lungo lo spessore, quelle della
    # cavita' (l'acqua) in mezzo: la mesh non ha sempre tutte e due le pelli di un
    # contenitore, e l'unione delle proiezioni prende quella che c'e'
    acqua = m & (q > q[m].min()) & (q < q[m].max())
    tri[f] = (T3[C == f][:, :, piano], T3[acqua][:, :, piano])
    # spessore dell'acqua: fra le due quote della cavita' con piu' triangoli (ce ne sono
    # di piccole, attorno ai fori, che non sono il soffitto ne' il pavimento)
    quote, quanti = np.unique(q[acqua], return_counts=True)
    qq = T3[acqua][:, :, ax].mean(1)
    due = sorted(np.median(qq[q[acqua] == v]) for v in quote[np.argsort(quanti)[-2:]])
    area[f] = dict(esterna_m2=area_unione(tri[f][0]), acqua_m2=area_unione(tri[f][1]),
                   spessore_acqua_mm=float(due[1] - due[0]) if len(due) == 2 else None)
A_ACQUA = sum(a["acqua_m2"] for a in area.values())
A_EST = sum(a["esterna_m2"] for a in area.values())
N_PMT = len(PMT)
for f in FACCE:
    print("%-3s acqua %6.3f m^2  inviluppo %6.3f m^2" % (f, area[f]["acqua_m2"], area[f]["esterna_m2"]))
print("totale: acqua %.2f m^2 (inviluppo %.2f); prototipo %.4f m^2 -> rapporto %.1f; PMT %d"
      % (A_ACQUA, A_EST, AREA_PROTO, A_ACQUA / AREA_PROTO, N_PMT))

# ------------------------------------------------------------------ rate del prototipo
SOGLIE = json.load(open(_os.path.join(MIS, "soglie_trigger_lngs.json")))
RUN = {"0013": "LNGS__54-0V_0013_750Ms_55PT", "0011": "LNGS__54-0V_0011_750Ms_55PT"}
SOGLIA_PE = {"0013": "1.8-2.2", "0011": "3.5-4.3"}   # offset 100 e 200, scan del 30/10 (soglie_pe_20261030.json)
MC = [_os.path.join(MIS, f) for f in ("mc_digit_gamma_righe_s3500.npz", "mc_digit_mu_lngs_s3500.npz")]

rate = {}
for r, nome in RUN.items():
    s = SOGLIE[nome]
    S, A, T = np.array(s["sigma_q"]), np.array(s["soglia"]), s["durata_s"]
    zd = np.load(_os.path.join(MIS, "carica_%s_testbeam.npz" % r))
    q = np.stack([zd["q_caen_ch20"], zd["q_caen_ch21"]], 1)
    sop = q > 3 * S
    n_tot, n_uno, n_due = len(q), int((sop[:, 0] ^ sop[:, 1]).sum()), int((sop[:, 0] & sop[:, 1]).sum())
    # MC (gamma di Hall A + muoni, fibre a 3500 fotoni/MeV) col trigger della run: gli
    # eventi con un PMT solo per luce che arriva a un'estremita' sola
    mc_uno, mc_trig, var_uno = 0.0, 0.0, 0.0
    for f in MC:
        zm = np.load(f)
        w0, rep = float(zm["peso_hz"]), int(zm["repliche"])
        trig = (zm["amp"][:, 0] >= A[0]) | (zm["amp"][:, 1] >= A[1])
        sm = zm["q"] > 3 * S
        uno = trig & (sm[:, 0] ^ sm[:, 1])
        mc_trig += trig.sum() * w0
        mc_uno += uno.sum() * w0
        var_uno += (uno.sum() * w0) ** 2 / max(uno.sum() / rep, 1)   # repliche correlate
    R_trig, R_uno = n_tot / T, n_uno / T
    R_propri = R_uno - mc_uno
    R_luce = R_trig - R_propri
    e_trig = np.sqrt(n_tot) / T
    e_propri = np.hypot(np.sqrt(n_uno) / T, np.sqrt(var_uno))
    k_a, k_n = A_ACQUA / AREA_PROTO, N_PMT / 2
    rate[r] = dict(run=nome, offset=s["offset"], soglia_pe=SOGLIA_PE[r], durata_s=T, eventi=n_tot,
                   eventi_un_pmt=n_uno, eventi_due_pmt=n_due, R_trigger_hz=R_trig, R_trigger_err=e_trig,
                   R_un_pmt_hz=R_uno, R_un_pmt_mc_hz=mc_uno, R_trigger_mc_hz=mc_trig,
                   R_propri_pmt_hz=R_propri, R_propri_err=e_propri, R_luce_hz=R_luce,
                   veto_tutto_area_hz=R_trig * k_a, veto_tutto_area_err=e_trig * k_a,
                   veto_separato_hz=R_propri * k_n + R_luce * k_a,
                   veto_separato_err=np.hypot(e_propri * (k_n - k_a), e_trig * k_a))
    print("%s (offset %d, %s PE): trigger %.3f +- %.3f Hz; un PMT %.3f (MC %.3f) -> propri dei PMT %.3f, luce %.3f; "
          "MC trigger %.3f" % (r, s["offset"][0], SOGLIA_PE[r], R_trig, e_trig, R_uno, mc_uno, R_propri, R_luce, mc_trig))
    print("     veto: tutto con l'area %.0f +- %.0f Hz; propri col numero di PMT %.0f +- %.0f Hz"
          % (rate[r]["veto_tutto_area_hz"], rate[r]["veto_tutto_area_err"], rate[r]["veto_separato_hz"],
             rate[r]["veto_separato_err"]))

json.dump(dict(cad=_os.path.join(arg.cad, "newcad_mesh.npz"), area_prototipo_m2=AREA_PROTO, area_acqua_m2=A_ACQUA,
               area_inviluppo_m2=A_EST, rapporto_aree=A_ACQUA / AREA_PROTO, n_pmt=N_PMT, facce=area, rate=rate),
          open(arg.out + ".json", "w"), indent=1, default=float)

# ------------------------------------------------------------------ figura 1: il veto e le sue aree
fig = plt.figure(figsize=(15, 6.9))
# in alto la vista 3D accanto a tetto e fondo, quadrati; sotto le quattro pareti, basse e larghe
gs = fig.add_gridspec(2, 4, height_ratios=[1, 0.45], wspace=0.22, hspace=0.38, top=0.89, bottom=0.1,
                      left=0.04, right=0.98)
POSTO = {"Zp": gs[0, 2], "Zm": gs[0, 3], "Xp": gs[1, 0], "Xm": gs[1, 1], "Yp": gs[1, 2], "Ym": gs[1, 3]}
a3 = fig.add_subplot(gs[0, :2], projection="3d")
for f in FACCE:
    a3.add_collection3d(Poly3DCollection(T3[C == f] / 1000, facecolor=COL[f], edgecolor="none", alpha=0.75))
a3.scatter(*(PMT.T / 1000), s=6, c="k", depthshade=False, label="PMT (%d)" % N_PMT)
lo, hi = V.min(0) / 1000, V.max(0) / 1000
a3.set_xlim(lo[0], hi[0]); a3.set_ylim(lo[1], hi[1]); a3.set_zlim(lo[2], hi[2])
a3.set_box_aspect(hi - lo, zoom=1.3)
a3.view_init(elev=24, azim=-58)
a3.set_zticks([-1.4, -0.7, 0])
a3.set_xlabel("X [m]", labelpad=6); a3.set_ylabel("Y [m]", labelpad=6); a3.set_zlabel("Z [m]", labelpad=4)
# niente legenda: i colori sono quelli dei pannelli delle facce, che hanno il nome nel titolo
a3.set_title("CAD 20260713-Assy-WC-Y.stp;  black dots: the %d PMTs" % N_PMT, fontsize=10, loc="left")
for f in FACCE:
    a = fig.add_subplot(POSTO[f])
    est, acq = tri[f]
    a.add_collection(PolyCollection(est / 1000, facecolor="#d9d9d9", edgecolor="none"))
    a.add_collection(PolyCollection(acq / 1000, facecolor=COL[f], edgecolor="none"))
    tutto = est.reshape(-1, 2) / 1000
    a.set_xlim(tutto[:, 0].min() - 0.1, tutto[:, 0].max() + 0.1)
    a.set_ylim(tutto[:, 1].min() - 0.1, tutto[:, 1].max() + 0.1)
    a.set_aspect("equal")
    nomi = "XYZ".replace(f[0], "")
    a.set_xlabel("%s [m]" % nomi[0], fontsize=8); a.set_ylabel("%s [m]" % nomi[1], fontsize=8)
    a.tick_params(labelsize=7)
    sp = area[f]["spessore_acqua_mm"]
    a.set_title("%s: %.2f m² of water%s" % (LAB[f], area[f]["acqua_m2"], ", %.0f mm" % sp if sp else ""),
                fontsize=9.5, loc="left")
fig.suptitle("CUPID WC veto: %.1f m² of water (container walls excluded; outer envelope %.1f m²) = %.1f × the "
             "prototype (0.96 × 0.36 m²); %d PMTs = %d × 2"
             % (A_ACQUA, A_EST, A_ACQUA / AREA_PROTO, N_PMT, N_PMT // 2), fontsize=11.5)
fig.text(0.5, 0.02, "each face projected on its plane;  colour: water (the cavity "
         "inside the 10 mm HDPE walls), holes included;  grey: the walls of the containers", ha="center",
         fontsize=9, color="#555")
fig.savefig(arg.out + "_area.png", dpi=110)
print("scritto", arg.out + "_area.png")

# ------------------------------------------------------------------ figura 2: dal prototipo al veto
fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 5.2), width_ratios=[1, 1.35])
ROSSO, BLU = "#c0504d", "#4f81bd"
xs = np.arange(len(RUN))
etich = ["run %s\noffset %d ≈ %s PE" % (r, rate[r]["offset"][0], rate[r]["soglia_pe"]) for r in RUN]
p = np.array([rate[r]["R_propri_pmt_hz"] for r in RUN]); l = np.array([rate[r]["R_luce_hz"] for r in RUN])
a1.bar(xs, l, 0.55, color=BLU, label="light in the water and fibres (γ, μ): scales with the area")
a1.bar(xs, p, 0.55, bottom=l, color=ROSSO, label="one PMT alone, not in the MC (PMT own pulses):\n"
       "scales with the number of PMTs")
# verifica della divisione: la parte di luce dei dati contro il rate di trigger del MC,
# che gli impulsi propri dei PMT non li ha
a1.hlines([rate[r]["R_trigger_mc_hz"] for r in RUN], xs - 0.36, xs + 0.36, color="k", lw=1.6, ls="--",
          label="MC trigger rate (γ + μ, no PMT own pulses)")
for x, r in zip(xs, RUN):
    a1.text(x, rate[r]["R_trigger_hz"] * 1.03, "%.2f Hz" % rate[r]["R_trigger_hz"], ha="center", va="bottom", fontsize=10)
a1.set_xticks(xs, etich)
a1.set_ylabel("trigger rate [Hz]")
a1.set_ylim(0, max(rate[r]["R_trigger_hz"] for r in RUN) * 1.5)
a1.legend(fontsize=8.5, frameon=False, loc="upper right")
a1.set_title("prototype at LNGS: 2 PMTs, %.3f m² of water; trigger = either PMT above threshold"
             % AREA_PROTO, fontsize=9.5, loc="left")
w = 0.36
for j, (chiave, nome, tinta) in enumerate((("veto_tutto_area", "all × area ratio (%.1f)" % (A_ACQUA / AREA_PROTO), "#7f7f7f"),
                                           ("veto_separato", "own pulses × %d, light × %.1f" % (N_PMT // 2, A_ACQUA / AREA_PROTO), "#404040"))):
    v = np.array([rate[r][chiave + "_hz"] for r in RUN]); e = np.array([rate[r][chiave + "_err"] for r in RUN])
    a2.bar(xs + (j - 0.5) * w, v, w * 0.92, yerr=e, color=tinta, capsize=3, label=nome)
    for x, vv in zip(xs + (j - 0.5) * w, v):
        a2.text(x, vv * 1.04 + 4, "%.0f Hz" % vv, ha="center", va="bottom", fontsize=10)
a2.set_xticks(xs, etich)
a2.set_ylabel("expected rate [Hz]")
a2.set_ylim(0, max(rate[r]["veto_tutto_area_hz"] for r in RUN) * 1.3)
a2.legend(fontsize=9, frameon=False, loc="upper right", title="extrapolation", title_fontsize=9)
a2.set_title("full veto: %d PMTs, %.1f m² of water; any PMT above threshold" % (N_PMT, A_ACQUA), fontsize=9.5, loc="left")
fig.suptitle("From the prototype at LNGS to the CUPID WC veto, same threshold on every PMT", fontsize=11.5)
fig.text(0.5, 0.01, "error bars: statistics only.  The PE value of offset 100 depends on the calibration estimator "
         "(1.8 or 2.2 PE); between offset 80 and 100 the single-PMT rate changes by a factor 2.4 (scan of 30/10)",
         ha="center", fontsize=8.5, color="#555")
fig.tight_layout(rect=(0, 0.04, 1, 1))
fig.savefig(arg.out + "_rate.png", dpi=110)
print("scritto", arg.out + "_rate.png")
