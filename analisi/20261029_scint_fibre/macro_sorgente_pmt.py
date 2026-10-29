"""Scrive le macro GPS per una sorgente gamma DENTRO i moduli dei PMT (K-40,
catena dell'U-238, catena del Th-232) nel progetto MC, e il numero di gamma
per decadimento in measurements/sorgente_pmt_linee.json.

    python3 analisi/20261029_scint_fibre/macro_sorgente_pmt.py PROGETTO_MC

Perche': i gamma di Hall A nel MC, con la scintillazione delle fibre, danno
~0.03 Hz di eventi ad alta carica contro ~0.30 Hz misurati. I moduli dei PMT
(Hamamatsu H14600-103: 22 x 22 x 25 mm, 32-40 g, col generatore di alta
tensione dentro) stanno appoggiati sui fasci di fibre, dove le fibre sono piu'
fitte, e il MC non li ha. Qui si misura quanti eventi da' ogni gamma emesso
dal volume del modulo: diviso per il rate mancante, dice quanta attivita'
servirebbe, e se e' plausibile.

Sorgente: volume uniforme di 22 x 22 x 25 mm sopra ciascun PMT (x = +-440 mm,
centro a z = 72.5 mm: la finestra sta sulla faccia del tank a z = 60 mm), due
sorgenti di uguale intensita' (moduli L e R), emissione isotropa. Valida per
le geometrie con i PMT a x = +-440 mm (single, e le WBC di serie).

Righe: intensita' per 100 decadimenti del capostipite in equilibrio secolare,
valori ENSDF arrotondati e scritti a memoria, sopra 100 keV (sotto non fanno
elettroni utili nemmeno in scintillazione). Bastano per un ordine di grandezza.
"""
import json
import os
import sys

PROG = sys.argv[1]
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DELTA = 0.00025      # MeV, mezza larghezza del bin di ogni riga

CATENE = {
    "K40": [(1460.8, 10.66)],
    "U238": [  # Pa-234m, Ra-226, Pb-214, Bi-214
        (766.4, 0.29), (1001.0, 0.84), (186.2, 3.6),
        (242.0, 7.27), (295.2, 18.4), (351.9, 35.6),
        (609.3, 45.5), (665.4, 1.53), (768.4, 4.89), (806.2, 1.26), (934.1, 3.1), (1120.3, 14.9),
        (1155.2, 1.63), (1238.1, 5.83), (1281.0, 1.43), (1377.7, 3.99), (1401.5, 1.33),
        (1408.0, 2.39), (1509.2, 2.13), (1729.6, 2.84), (1764.5, 15.3), (1847.4, 2.03),
        (2118.5, 1.16), (2204.1, 4.91), (2447.9, 1.55)],
    "Th232": [  # Ac-228, Ra-224, Pb-212, Bi-212, Tl-208 (ramo del 35.94%)
        (129.1, 2.42), (209.3, 3.89), (270.2, 3.46), (328.0, 2.95), (338.3, 11.27), (409.5, 1.92),
        (463.0, 4.40), (794.9, 4.25), (835.7, 1.61), (911.2, 25.8), (964.8, 4.99), (969.0, 15.8),
        (1588.2, 3.22), (1630.6, 1.51), (241.0, 4.10), (238.6, 43.6), (300.1, 3.3),
        (727.3, 6.67), (785.4, 1.10), (1620.5, 1.47),
        (277.4, 6.6 * 0.3594), (510.8, 22.6 * 0.3594), (583.2, 85.0 * 0.3594),
        (860.6, 12.5 * 0.3594), (2614.5, 99.75 * 0.3594)],
}


def sorgente(x_mm, righe):
    r = ["/gps/particle gamma",
         "/gps/pos/type Volume",
         "/gps/pos/shape Para",
         "/gps/pos/centre %.1f 0 72.5 mm" % x_mm,
         "/gps/pos/halfx 11 mm",
         "/gps/pos/halfy 11 mm",
         "/gps/pos/halfz 12.5 mm",
         "/gps/ang/type iso",
         "/gps/ene/type User",
         "/gps/hist/type energy"]
    tot = sum(i for _, i in righe)
    for e, i in sorted(righe):
        r.append("/gps/hist/point %.6f 0" % (e / 1000 - DELTA))
        r.append("/gps/hist/point %.6f %.6e" % (e / 1000 + DELTA, i / tot))
    return r


info = {}
for nome, righe in CATENE.items():
    g = sum(i for _, i in righe) / 100.0
    info[nome] = {"gamma_per_decadimento": g, "righe_keV_percento": righe}
    testo = ["# =======================================================",
             "# SORGENTE: gamma della catena %s DENTRO i moduli dei PMT" % nome,
             "# Generata da daq-standalone/analisi/20261029_scint_fibre/macro_sorgente_pmt.py",
             "# Volume 22 x 22 x 25 mm sopra ciascun PMT (x = +-440 mm, z = 60-85 mm),",
             "# due sorgenti uguali (L e R), isotropa. %.3f gamma per decadimento" % g,
             "# del capostipite (righe sopra 100 keV, equilibrio secolare).",
             "# Da includere DOPO /run/initialize.",
             "# ======================================================="]
    testo += ["/gps/source/intensity 1"] + sorgente(440.0, righe)
    testo += ["/gps/source/add 1"] + sorgente(-440.0, righe)
    p = os.path.join(PROG, "gun_gammas_pmt_%s.mac" % nome)
    open(p, "w").write("\n".join(testo) + "\n")
    print("scritto %s: %d righe, %.3f gamma per decadimento" % (p, len(righe), g))
json.dump(info, open(os.path.join(ROOT, "measurements", "sorgente_pmt_linee.json"), "w"), indent=1)
print("scritto measurements/sorgente_pmt_linee.json")
