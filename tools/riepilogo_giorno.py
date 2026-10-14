#!/usr/bin/env python3
"""
Il riepilogo di fine giornata: le run prese, gli eventi, il rate.

    python3 tools/riepilogo_giorno.py --giorno 2026-10-13 --png /tmp/r.png
    python3 tools/riepilogo_giorno.py --giorno 2026-10-13 --invia luigi.benussi@lnf.infn.it

Legge i .log delle run come fa l'e-log (tools/elog.py), non gli HDF5: sono gli
stessi numeri e si leggono in un istante.

Il grafico del rate ha UN segmento per run, all'altezza del suo rate medio.
Dentro una run non si vede come varia, e non per scelta: il file HDF5 non
registra l'istante di ogni evento, e il log ne dichiara solo il totale e la
durata. Per una curva continua serve che la DAQ scriva il tempo dell'evento.
"""
import argparse
import html
import io
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import elog   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_del_giorno(cartelle_dati, giorno):
    """(run fuori dagli scan, numero di run di scan) iniziate quel giorno.

    Piu' cartelle e non una: OutputDir si cambia dalla pagina, e le run di
    una giornata possono stare in data/ e altrove. Il registro
    dell'alimentatore e delle azioni sta comunque in data/.
    """
    if isinstance(cartelle_dati, str):
        cartelle_dati = [cartelle_dati]
    base = os.path.join(ROOT, "data")
    psu = elog.leggi_psu(os.path.join(base, "psu-log.csv"))
    avvii = elog.leggi_avvii(os.path.join(base, "azioni.jsonl"))
    righe, n_scan, visti = [], 0, set()
    for cartella in cartelle_dati:
        for radice, cartelle, files in os.walk(cartella):
            in_scan = os.sep + "scan" in radice[len(cartella):]
            for f in files:
                if not f.endswith(".log") or f.startswith(("daq-console", "controllore",
                                                            "monitor-console", "scan-console")):
                    continue
                percorso = os.path.realpath(os.path.join(radice, f))
                if percorso in visti:        # una cartella dentro l'altra
                    continue
                visti.add(percorso)
                r = elog.riga_da_log(percorso, psu, avvii)
                if not r or r["data"] != giorno:
                    continue
                if in_scan:
                    n_scan += 1
                else:
                    righe.append(r)
    righe.sort(key=lambda r: r["ora"])
    return righe, n_scan


def _secondi(r):
    return float(r["durata_s"]) if r["durata_s"] != "" else 0.0


def _inizio_h(r):
    h, m, s = (int(x) for x in r["ora"].split(":"))
    return h + m / 60 + s / 3600


def grafico_rate(righe, giorno):
    """PNG del rate medio di ogni run sull'asse delle ore del giorno."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker  # noqa: F401

    fig, ax = plt.subplots(figsize=(8.0, 3.0), dpi=110)
    alto = 0.0
    for r in righe:
        x0 = _inizio_h(r)
        x1 = x0 + _secondi(r) / 3600
        if r["rate_hz"] == "":
            # Run senza riepilogo finale: non ha un rate da disegnare, ma deve
            # vedersi che c'e' stata, se no il grafico racconta una giornata
            # piu' pulita di com'e' andata.
            ax.axvline(x0, color="#a8321f", lw=1, ls=":")
            continue
        rate = float(r["rate_hz"])
        alto = max(alto, rate)
        colore = "#15603a" if r["chiusa"] == "si" else "#a8321f"
        if x1 - x0 < 0.08:
            # Sotto i cinque minuti il segmento sarebbe un trattino largo un
            # pixel: un punto si vede, e dice lo stesso.
            ax.plot([(x0 + x1) / 2], [rate], "o", ms=3.5, color=colore)
        else:
            ax.plot([x0, x1], [rate, rate], lw=4, color=colore, solid_capstyle="butt")
    if len(righe) <= 25:
        for r in righe:
            if r["rate_hz"] != "":
                num = r["run"].split("_")[2] if r["run"].count("_") >= 2 else r["run"]
                ax.annotate(num, (_inizio_h(r) + _secondi(r) / 7200, float(r["rate_hz"])),
                            xytext=(0, 4), textcoords="offset points",
                            ha="center", fontsize=7, color="#6b6a65")

    # Lo zero sempre sull'asse: un rate dimezzato e uno intatto devono essere
    # due immagini diverse, e con l'autoscala sui soli punti non lo sono.
    ax.set_ylim(0, alto * 1.25 if alto > 0 else 1)
    if righe:
        a = min(_inizio_h(r) for r in righe)
        b = max(_inizio_h(r) + _secondi(r) / 3600 for r in righe)
        ax.set_xlim(max(0, int(a) - 0.25), min(24, int(b) + 1.25))
    else:
        ax.set_xlim(0, 24)
    ax.xaxis.set_major_locator(matplotlib.ticker.MultipleLocator(
        1 if ax.get_xlim()[1] - ax.get_xlim()[0] <= 8 else 2))
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(
        lambda v, _: "%02d:00" % int(round(v))))
    ax.set_xlabel("time of day")
    ax.set_ylabel("mean rate per run [Hz]")
    ax.set_title("DAQ rate, %s  (green: closed cleanly, red: not)" % giorno, fontsize=10)
    ax.grid(alpha=.3)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def _durata(s):
    s = int(round(s))
    if s < 60:
        return "%d s" % s
    return "%d h %02d min" % (s // 3600, (s % 3600) // 60) if s >= 3600 else "%d min" % (s // 60)


def _gb(b):
    return "%.1f GB" % (b / 1e9)


def componi_riepilogo(cartelle_dati, giorno):
    """(oggetto, testo, html, png, numero di run) del riepilogo di quel giorno."""
    righe, n_scan = run_del_giorno(cartelle_dati, giorno)
    if isinstance(cartelle_dati, str):
        cartelle_dati = [cartelle_dati]
    eventi = sum(int(r["eventi"]) for r in righe if r["eventi"] != "")
    secondi = sum(_secondi(r) for r in righe)
    byte = sum(int(r["byte"]) for r in righe if r["byte"] != "")
    male = [r for r in righe if r["chiusa"] == "NO"]
    disco = shutil.disk_usage(cartelle_dati[0])

    oggetto = "[DAQ V1742] Daily summary %s — %d runs, %s events" % (
        giorno, len(righe), format(eventi, ",").replace(",", " "))

    intest = ("run", "start", "duration", "events", "rate", "HV", "closed")
    tab = []
    for r in righe:
        tab.append((r["run"], r["ora"][:5],
                    _durata(_secondi(r)) if r["durata_s"] != "" else "—",
                    format(int(r["eventi"]), ",").replace(",", " ") if r["eventi"] != "" else "—",
                    "%.0f Hz" % float(r["rate_hz"]) if r["rate_hz"] != "" else "—",
                    "%.1f V" % float(r["hv_v"]) if r["hv_v"] != "" else "—",
                    {"si": "yes", "NO": "NO"}.get(r["chiusa"], r["chiusa"])))

    totale = "Total: %d runs, %s events, %s of acquisition, %s" % (
        len(righe), format(eventi, ",").replace(",", " "), _durata(secondi), _gb(byte))
    note = []
    if male:
        note.append("Not closed cleanly: %d (%s)" % (len(male), ", ".join(r["run"] for r in male)))
    if n_scan:
        note.append("Threshold scan runs: %d (in data/scan/, not listed)" % n_scan)
    note.append("Disk: %s free of %s (%.0f%%)" % (
        _gb(disco.free), _gb(disco.total), 100.0 * disco.free / disco.total))
    note.append("Generated on the DAQ PC at %s" % time.strftime("%Y-%m-%d %H:%M:%S %Z"))
    note.append("Rates are the DAQ's own: above ~900 Hz it saturates and the true rate is higher.")

    larg = [max(len(intest[i]), *(len(t[i]) for t in tab)) if tab else len(intest[i])
            for i in range(len(intest))]
    fmt = "  ".join("%%-%ds" % w for w in larg)
    testo = "Runs started on %s (data/, scan runs excluded)\n\n" % giorno
    if tab:
        testo += fmt % intest + "\n" + "\n".join(fmt % t for t in tab) + "\n\n"
    else:
        testo += "No runs.\n\n"
    testo += totale + "\n" + "\n".join(note) + "\n"

    td = 'style="padding:2px 12px 2px 0;font-variant-numeric:tabular-nums"'
    righe_html = "".join(
        "<tr>%s</tr>" % "".join(
            '<td %s%s>%s</td>' % (td, ' bgcolor="#f8e0da"' if t[6] == "NO" else "", html.escape(c))
            for c in t)
        for t in tab)
    html_ = """<html><body style="font:14px/1.45 system-ui,sans-serif;color:#1a1a19">
<p>Runs started on <b>%s</b> (data/, scan runs excluded)</p>
<img src="cid:rate" alt="rate per run" style="max-width:100%%">
<table style="border-collapse:collapse;font-size:13px;margin-top:10px">
<tr>%s</tr>%s</table>
<p><b>%s</b><br>%s</p>
</body></html>""" % (
        giorno,
        "".join('<td %s><b>%s</b></td>' % (td, h) for h in intest),
        righe_html, html.escape(totale), "<br>".join(html.escape(n) for n in note))

    return oggetto, testo, html_, grafico_rate(righe, giorno), len(righe)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-d", "--data-dir", default=os.path.join(ROOT, "data"))
    ap.add_argument("--giorno", default=time.strftime("%Y-%m-%d"), help="AAAA-MM-GG, di serie oggi")
    ap.add_argument("--png", help="salva il grafico qui")
    ap.add_argument("--invia", nargs="+", metavar="INDIRIZZO", help="spedisce il riepilogo")
    a = ap.parse_args()

    oggetto, testo, html_, png, _ = componi_riepilogo(a.data_dir, a.giorno)
    print(oggetto + "\n\n" + testo)
    if a.png:
        open(a.png, "wb").write(png)
        print("grafico in", a.png)
    if a.invia:
        import posta
        m = posta.componi(oggetto, testo, a.invia, html=html_, immagini=[("rate", png)])
        print("spedisco alle %s" % time.strftime("%H:%M:%S %Z"))
        ok, ko = posta.invia(m, a.invia)
        for x in ok:
            print("consegnata a", x)
        for x, motivo in ko.items():
            print("NON consegnata a %s: %s" % (x, motivo))


if __name__ == "__main__":
    main()
