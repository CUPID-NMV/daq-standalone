#!/usr/bin/env python3
"""
Versa le righe automatiche dell'e-log in un foglio Google.

    python3 tools/elog_push.py runs_auto.csv --chiave <id del foglio>

Il CSV lo produce `tools/elog.py` sulla macchina DAQ; questo gira dove stanno
le credenziali (il Mac), perche' una chiave di servizio non va copiata in giro.

Due fogli, e la separazione e' il punto:

  runs_auto   una riga per run, solo roba che la DAQ sa. Le run del CSV si
              aggiornano, le altre restano: il CSV puo' contenere solo le
              ultime N run.
  elog        le colonne a mano -- scopo, sorgente, setup, esito, note -- piu'
              i dati richiamati per nome della run.

I nomi delle run nell'e-log restano VALORI, non formule, e le run nuove si
aggiungono in fondo: cosi' una rigenerazione non puo' spostare le righe sotto
le annotazioni gia' scritte. E' l'errore che rende inservibile un e-log, e si
scopre mesi dopo quando le note non corrispondono piu' alle run.
"""
import argparse
import csv
import os
import sys

# Colonne richiamate da runs_auto, nell'ordine in cui compaiono nell'e-log:
# (titolo, indice di colonna in runs_auto a partire da 1)
RICHIAMI = [("data", 2), ("ora", 3), ("HV [V]", 4), ("I [uA]", 5),
            ("channels", 6), ("trigger", 7), ("thresholds", 8),
            ("events", 13), ("rate [Hz]", 14), ("closed", 16)]

# Quello che solo chi era al banco puo' scrivere. "setup" e' una colonna sua e
# non una nota: quale rivelatore su quale canale, quale ingresso del CFD,
# sdoppiatori e attenuatori non stanno in nessun file -- stanno nei cavi.
A_MANO = ["purpose", "source", "setup", "outcome", "notes", "operator"]

# "operator" si riempie da solo con chi ha premuto Start, ma resta una colonna
# A MANO: il nome entra come VALORE, non come formula, cosi' lo si puo'
# correggere -- il controllore sa chi ha avviato la coda, non chi era al banco
# tre ore dopo -- e nessuna rigenerazione lo riscrive sopra.
COL_OPERATORE_IN_CSV = 17


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", help="il CSV prodotto da tools/elog.py")
    ap.add_argument("--chiave", required=True, help="id del foglio Google")
    ap.add_argument("--credenziali",
                    default=os.path.expanduser("~/Downloads/.google_credentials.json"))
    ap.add_argument("--rifai", action="store_true",
                    help="ricrea l'e-log da zero: CANCELLA le colonne scritte a mano")
    a = ap.parse_args()

    import gspread
    gc = gspread.service_account(filename=a.credenziali)
    sh = gc.open_by_key(a.chiave)

    righe = list(csv.reader(open(a.csv)))
    if len(righe) < 2:
        sys.exit("il CSV e' vuoto")

    # --- 1. runs_auto: le run del CSV si aggiornano, le altre RESTANO ---------
    # Il controllore versa solo le ultime 20 run a ogni fine run. Riscrivendo
    # il foglio da zero, le run piu' vecchie sparivano da runs_auto e nell'e-log
    # i loro dati richiamati per nome restavano vuoti, finche' qualcuno non
    # lanciava un sync completo. Il CSV dice quali run AGGIORNARE, non quali
    # devono esistere.
    try:
        ws = sh.worksheet("runs_auto")
        vecchie = ws.get_all_values()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="runs_auto", rows=len(righe) + 500,
                              cols=len(righe[0]) + 2)
        vecchie = []
    intest = righe[0]
    tutte = {}
    if len(vecchie) > 1:
        # Per nome di colonna: se elog.py guadagna una colonna, le run vecchie
        # restano al loro posto con la casella nuova vuota.
        vint = vecchie[0]
        for r in vecchie[1:]:
            if r and r[0]:
                tutte[r[0]] = [r[vint.index(c)] if c in vint and vint.index(c) < len(r) else ""
                               for c in intest]
    for r in righe[1:]:
        tutte[r[0]] = r
    ordinate = sorted(tutte.values(), key=lambda r: (r[1], r[2], r[0]))
    righe = [intest] + ordinate
    if ws.row_count < len(righe) + 10:
        ws.add_rows(len(righe) + 100 - ws.row_count)
    ws.clear()
    ws.update(values=righe, range_name="A1")
    ws.freeze(rows=1)

    # --- 2. elog: si aggiungono SOLO le run nuove ---------------------------
    nomi = [r[0] for r in righe[1:]]
    if a.rifai:
        # Solo su richiesta esplicita: qui dentro ci sono le annotazioni di
        # chi era al banco, e nessuno script deve poterle buttare per conto suo.
        try:
            sh.del_worksheet(sh.worksheet("elog"))
        except gspread.WorksheetNotFound:
            pass
    try:
        el = sh.worksheet("elog")
        gia = [v for v in el.col_values(1)[1:] if v]
    except gspread.WorksheetNotFound:
        el = sh.add_worksheet(title="elog", rows=len(nomi) + 500,
                              cols=1 + len(RICHIAMI) + len(A_MANO) + 2)
        el.update(values=[["run"] + [t for t, _ in RICHIAMI] + A_MANO],
                  range_name="A1")
        el.freeze(rows=1)
        gia = []

    nuove = [n for n in nomi if n not in set(gia)]
    if nuove:
        prima = len(gia) + 2
        if el.row_count < prima + len(nuove) + 10:
            el.add_rows(prima + len(nuove) + 100 - el.row_count)
        operatore = {r[0]: (r[COL_OPERATORE_IN_CSV - 1]
                            if len(r) >= COL_OPERATORE_IN_CSV else "")
                     for r in righe[1:]}
        blocco = []
        for k, nome in enumerate(nuove):
            r = prima + k
            # VLOOKUP sul NOME, non sulla posizione: runs_auto puo' essere
            # riordinato o riscritto senza che queste righe si spostino.
            blocco.append([nome] + [
                '=IFERROR(VLOOKUP($A%d;runs_auto!$A:$R;%d;FALSE);"")' % (r, col)
                for _, col in RICHIAMI]
                + [""] * (len(A_MANO) - 1) + [operatore.get(nome, "")])
        el.update(values=blocco, range_name="A%d" % prima,
                  value_input_option="USER_ENTERED")
    abbellisci(sh, el, len(nomi))
    print("runs_auto: %d run. elog: %d gia' presenti, %d aggiunte."
          % (len(nomi), len(gia), len(nuove)))


def abbellisci(sh, el, n_righe):
    """Quello che rende il foglio usabile tutti i giorni invece che solo pieno.

    Si riapplica a ogni giro: costa una chiamata e rimette a posto quello che
    qualcuno potrebbe aver tolto senza volerlo.
    """
    ultima = n_righe + 1
    el.freeze(rows=1)
    el.format("A1:Q1", {"textFormat": {"bold": True},
                        "backgroundColor": {"red": .93, "green": .93, "blue": .92}})
    try:
        el.set_basic_filter()          # filtri e ordinamento dalle intestazioni
    except Exception:
        pass

    # Una run non chiusa si deve vedere da lontano: niente eventi, niente rate,
    # e chi legge deve saperlo prima di cercarli.
    try:
        sh.batch_update({"requests": [{
            "addConditionalFormatRule": {"rule": {
                "ranges": [{"sheetId": el.id, "startRowIndex": 1,
                            "endRowIndex": ultima, "startColumnIndex": 0,
                            "endColumnIndex": 17}],
                "booleanRule": {
                    "condition": {"type": "CUSTOM_FORMULA",
                                  "values": [{"userEnteredValue": '=$K2="NO"'}]},
                    "format": {"backgroundColor": {"red": .99, "green": .91, "blue": .89}}}},
                "index": 0}}]})
    except Exception:
        pass

    # L'esito da una tendina: tre parole sempre uguali si filtrano, tre modi di
    # scrivere "buona" no.
    try:
        sh.batch_update({"requests": [{
            "setDataValidation": {
                "range": {"sheetId": el.id, "startRowIndex": 1, "endRowIndex": ultima + 200,
                          "startColumnIndex": 14, "endColumnIndex": 15},
                "rule": {"condition": {"type": "ONE_OF_LIST", "values": [
                             {"userEnteredValue": v} for v in
                             ("good", "bad", "test", "to check")]},
                         "showCustomUi": True, "strict": False}}}]})
    except Exception:
        pass


if __name__ == "__main__":
    main()
