#!/usr/bin/env python3
"""
Monitor online con i grafici dei segnali, servito via HTTP.

Avvia un piccolo server sulla macchina DAQ che rigenera i grafici dagli
ultimi eventi acquisiti e li mostra in una pagina che si aggiorna da sola.
Pensato per l'uso con VS Code Remote-SSH, che inoltra la porta sul locale.

    python3 tools/live_monitor.py              # porta 8765, ultimo file in data/
    python3 tools/live_monitor.py -p 9000 -n 30

Poi apri http://localhost:8765 nel browser.

Usa solo la libreria standard piu' numpy/h5py/matplotlib: niente Flask.
"""

import argparse
import glob
import io
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import numpy as np

import matplotlib
matplotlib.use("Agg")          # nessun display: si generano solo PNG
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daqio import load, DaqFileError


# ----------------------------------------------------------------------
#  Stato condiviso
# ----------------------------------------------------------------------

class Monitor:
    """Tiene l'ultimo set di dati letto e ne calcola rate e statistiche.

    I dati vengono riletti al massimo ogni `min_interval` secondi: il server
    e' multi-thread e senza questa cache ogni immagine richiesta dalla pagina
    farebbe una rilettura completa del file.
    """

    def __init__(self, data_dir, path=None, max_events=200, min_interval=2.0):
        self.data_dir = data_dir
        self.fixed_path = path
        self.max_events = max_events
        self.min_interval = min_interval

        self.lock = threading.Lock()      # matplotlib non e' thread-safe
        self.hdr = None
        self.data = None
        self.error = None
        self.path = None

        self.n_events = 0
        self.rate = 0.0
        self._last_read = 0.0
        self._prev = None                 # (n_eventi, timestamp)

    # -- lettura -------------------------------------------------------

    def _latest_file(self):
        if self.fixed_path:
            return self.fixed_path
        files = glob.glob(os.path.join(self.data_dir, "*.h5"))
        if not files:
            raise DaqFileError(f"Nessun file .h5 in {self.data_dir}")
        return max(files, key=os.path.getmtime)

    def refresh(self, force=False):
        now = time.time()
        if not force and (now - self._last_read) < self.min_interval:
            return
        self._last_read = now

        try:
            path = self._latest_file()
            hdr, data = load(path, max_events=None, live=True)
        except DaqFileError as exc:
            self.error = str(exc).splitlines()[0]
            return
        except Exception as exc:                      # pragma: no cover
            self.error = f"{type(exc).__name__}: {exc}"
            return

        self.error = None
        self.path = path
        total = data.shape[0]

        if self._prev is not None:
            prev_n, prev_t = self._prev
            dt = now - prev_t
            if dt > 0 and total >= prev_n:
                self.rate = (total - prev_n) / dt
        self._prev = (total, now)

        self.n_events = total
        self.hdr = hdr
        # Per i grafici bastano gli ultimi eventi: il file puo' diventare grosso
        self.data = data[-self.max_events:]

    # -- analisi -------------------------------------------------------

    def analysis(self):
        """(hdr, corr, amp, t_ns) oppure None se non ci sono ancora dati."""
        if self.data is None or self.data.shape[0] == 0:
            return None

        data = self.data
        n_pre = max(4, int(data.shape[2] * 0.15))
        base = np.median(data[:, :, :n_pre], axis=2)
        corr = data - base[:, :, None]

        imax, imin = np.max(corr, axis=2), np.min(corr, axis=2)
        amp = np.where(np.abs(imin) > np.abs(imax), imin, imax)

        dt_ns = float(self.hdr.get("SamplingTime", 1e-9)) * 1e9
        t_ns = np.arange(data.shape[2]) * dt_ns
        return self.hdr, base, corr, amp, t_ns

    def stats(self):
        out = {
            "file": os.path.basename(self.path) if self.path else None,
            "events": int(self.n_events),
            "rate": round(self.rate, 2),
            "error": self.error,
            "shown": 0,
            "channels": [],
        }
        res = self.analysis()
        if res is None:
            return out

        hdr, base, corr, amp, _ = res
        n_pre = int(self.data.shape[2] * 0.15)
        out["shown"] = int(self.data.shape[0])
        out["sampling"] = str(hdr.get("SamplingRate", "?"))
        for i, ch in enumerate(hdr["ChannelList"]):
            out["channels"].append({
                "ch": int(ch),
                "baseline": round(float(np.mean(base[:, i])), 1),
                "rms": round(float(np.std(self.data[:, i, :n_pre])), 2),
                "amp_mean": round(float(np.mean(amp[:, i])), 1),
                "amp_max": round(float(np.max(np.abs(amp[:, i]))), 1),
            })
        return out

    # -- grafici -------------------------------------------------------

    def figure(self, kind, n_show=1, xlim=(None, None), ylim=(None, None)):
        res = self.analysis()
        if res is None:
            return self._placeholder()
        hdr, _, corr, amp, t_ns = res
        channels = hdr["ChannelList"]

        def apply_limits(ax):
            """Limiti espliciti dove indicati, autoscale dove no."""
            if xlim[0] is not None or xlim[1] is not None:
                ax.set_xlim(left=xlim[0], right=xlim[1])
            if ylim[0] is not None or ylim[1] is not None:
                ax.set_ylim(bottom=ylim[0], top=ylim[1])

        if kind == "waveforms":
            fig, axes = plt.subplots(len(channels), 1, figsize=(9, 2.9 * len(channels)),
                                     squeeze=False, sharex=True)
            n = max(1, min(n_show, corr.shape[0]))
            for i, ch in enumerate(channels):
                ax = axes[i][0]
                for e in range(corr.shape[0] - n, corr.shape[0]):
                    ax.plot(t_ns, corr[e, i], lw=0.9 if n == 1 else 0.6,
                            alpha=1.0 if n == 1 else 0.5)
                ax.axhline(0, color="k", lw=0.8, ls=":")
                label = ("ultimo evento" if n == 1 else f"ultimi {n} eventi")
                ax.set_title(f"ch{ch} — {label}", fontsize=10)
                ax.set_ylabel("ADC − baseline")
                ax.grid(alpha=0.25)
                apply_limits(ax)
            axes[-1][0].set_xlabel("tempo [ns]")

        elif kind == "average":
            fig, ax = plt.subplots(figsize=(9, 4))
            for i, ch in enumerate(channels):
                ax.plot(t_ns, corr[:, i].mean(axis=0), lw=1.4, label=f"ch{ch}")
            ax.axhline(0, color="k", lw=0.8, ls=":")
            ax.set_xlabel("tempo [ns]")
            ax.set_ylabel("ADC − baseline")
            ax.set_title(f"Media su {corr.shape[0]} eventi", fontsize=10)
            ax.legend()
            ax.grid(alpha=0.25)
            apply_limits(ax)

        else:   # amplitudes
            fig, axes = plt.subplots(1, len(channels), figsize=(5 * len(channels), 3.4),
                                     squeeze=False)
            for i, ch in enumerate(channels):
                ax = axes[0][i]
                ax.hist(amp[:, i], bins=min(50, max(10, amp.shape[0] // 3)))
                ax.set_xlabel("ampiezza di picco [ADC]")
                ax.set_ylabel("eventi")
                ax.set_title(f"ch{ch}", fontsize=10)
                ax.grid(alpha=0.25)

        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        plt.close(fig)
        return buf.getvalue()

    def _placeholder(self):
        fig, ax = plt.subplots(figsize=(9, 2.5))
        ax.text(0.5, 0.5, self.error or "In attesa di eventi…",
                ha="center", va="center", fontsize=12, wrap=True)
        ax.axis("off")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        plt.close(fig)
        return buf.getvalue()


# ----------------------------------------------------------------------
#  HTTP
# ----------------------------------------------------------------------

PAGE = """<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8">
<title>DAQ V1742 — monitor</title>
<style>
  :root { --bg:#fff; --fg:#1a1a1a; --mut:#666; --line:#e0e0e0; --acc:#0a7; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#16181d; --fg:#e8e8e8; --mut:#999; --line:#2c2f36; --acc:#2db88a; }
  }
  body { background:var(--bg); color:var(--fg); margin:0; padding:20px;
         font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
  h1 { font-size:18px; margin:0 0 4px; }
  .sub { color:var(--mut); font-size:13px; margin-bottom:16px; }
  .bar { display:flex; gap:24px; flex-wrap:wrap; align-items:baseline;
         border:1px solid var(--line); border-radius:8px; padding:12px 16px; margin-bottom:16px; }
  .bar b { font-size:22px; color:var(--acc); font-variant-numeric:tabular-nums; }
  .bar span { color:var(--mut); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
  table { border-collapse:collapse; margin-bottom:20px; font-variant-numeric:tabular-nums; }
  th,td { padding:6px 14px; text-align:right; border-bottom:1px solid var(--line); }
  th { color:var(--mut); font-weight:500; font-size:12px; text-transform:uppercase; }
  td:first-child,th:first-child { text-align:left; }
  img { max-width:100%; border:1px solid var(--line); border-radius:8px; margin-bottom:16px;
        background:#fff; }
  .err { color:#c33; }
  .ctl { display:flex; gap:16px; flex-wrap:wrap; align-items:flex-end;
         border:1px solid var(--line); border-radius:8px; padding:12px 16px; margin-bottom:16px; }
  .ctl label { display:flex; flex-direction:column; gap:4px; font-size:12px; color:var(--mut); }
  .ctl input { width:82px; padding:5px 7px; font:13px inherit; color:var(--fg);
               background:var(--bg); border:1px solid var(--line); border-radius:5px; }
  .ctl button { padding:6px 14px; font:13px inherit; border-radius:5px; cursor:pointer;
                border:1px solid var(--line); background:var(--bg); color:var(--fg); }
  .ctl .hint { color:var(--mut); font-size:12px; }
</style></head><body>
<h1>DAQ V1742 — monitor online</h1>
<div class="sub" id="file">…</div>
<div class="bar">
  <div><b id="rate">–</b> <span>Hz</span></div>
  <div><b id="events">–</b> <span>eventi</span></div>
  <div><b id="shown">–</b> <span>nei grafici</span></div>
  <div><span id="err" class="err"></span></div>
</div>
<div class="ctl">
  <label>x min [ns]<input id="xmin" value="__XMIN__" placeholder="auto"></label>
  <label>x max [ns]<input id="xmax" value="__XMAX__" placeholder="auto"></label>
  <label>y min [ADC]<input id="ymin" value="__YMIN__" placeholder="auto"></label>
  <label>y max [ADC]<input id="ymax" value="__YMAX__" placeholder="auto"></label>
  <label>eventi<input id="nev" value="__NEVENTS__" style="width:60px"></label>
  <button id="reset">Autoscale</button>
  <span class="hint">campi vuoti = autoscale · si applica al giro successivo</span>
</div>
<table id="tab"><thead><tr><th>canale</th><th>baseline</th><th>rms</th>
<th>ampiezza media</th><th>max</th></tr></thead><tbody></tbody></table>
<img id="w" alt="forme d'onda"><img id="a" alt="media"><img id="h" alt="ampiezze">
<script>
const REFRESH = __REFRESH__ * 1000;
const FIELDS = ['xmin','xmax','ymin','ymax','nev'];

// I limiti scelti sopravvivono a un reload della pagina. localStorage puo'
// essere inaccessibile (finestra privata, cookie bloccati): mai fatale.
try {
  for (const f of FIELDS) {
    const v = localStorage.getItem('daqmon.' + f);
    if (v !== null) document.getElementById(f).value = v;
  }
} catch (e) {}

function params() {
  const p = new URLSearchParams();
  for (const f of FIELDS) {
    const v = document.getElementById(f).value.trim();
    try { localStorage.setItem('daqmon.' + f, v); } catch (e) {}
    if (v !== '') p.set(f === 'nev' ? 'n' : f, v);
  }
  return p;
}

document.getElementById('reset').onclick = () => {
  for (const f of ['xmin','xmax','ymin','ymax']) document.getElementById(f).value = '';
  tick();
};
for (const f of FIELDS)
  document.getElementById(f).addEventListener('change', tick);
async function tick() {
  try {
    const s = await (await fetch('stats.json', {cache:'no-store'})).json();
    document.getElementById('rate').textContent   = s.rate ?? '–';
    document.getElementById('events').textContent = s.events ?? '–';
    document.getElementById('shown').textContent  = s.shown ?? '–';
    document.getElementById('err').textContent    = s.error || '';
    document.getElementById('file').textContent   =
      (s.file || 'nessun file') + (s.sampling ? ' · ' + s.sampling : '');
    const tb = document.querySelector('#tab tbody');
    tb.innerHTML = (s.channels||[]).map(c =>
      `<tr><td>ch${c.ch}</td><td>${c.baseline}</td><td>${c.rms}</td>
       <td>${c.amp_mean}</td><td>${c.amp_max}</td></tr>`).join('');
    const p = params();
    p.set('t', Date.now());
    for (const [id, name] of [['w','waveforms'],['a','average'],['h','amplitudes']])
      document.getElementById(id).src = name + '.png?' + p.toString();
  } catch (e) { document.getElementById('err').textContent = 'server non raggiungibile'; }
}
tick(); setInterval(tick, REFRESH);
</script></body></html>"""


def _fmt(v):
    """Valore per un campo della pagina: vuoto se non impostato."""
    return "" if v is None else str(v)


def make_handler(monitor, refresh, defaults):

    class Handler(BaseHTTPRequestHandler):

        def log_message(self, *a):         # niente log di accesso sul terminale
            pass

        @staticmethod
        def _num(qs, key, default=None):
            """Un parametro numerico dalla query string; vuoto o invalido = default."""
            raw = qs.get(key, [""])[0].strip()
            if raw == "":
                return default
            try:
                return float(raw)
            except ValueError:
                return default

        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            route = parsed.path.lstrip("/") or "index.html"
            qs = parse_qs(parsed.query)

            if route == "index.html":
                page = (PAGE.replace("__REFRESH__", str(refresh))
                            .replace("__NEVENTS__", str(defaults["n"]))
                            .replace("__XMIN__", _fmt(defaults["xmin"]))
                            .replace("__XMAX__", _fmt(defaults["xmax"]))
                            .replace("__YMIN__", _fmt(defaults["ymin"]))
                            .replace("__YMAX__", _fmt(defaults["ymax"])))
                return self._send(200, "text/html; charset=utf-8", page.encode())

            # I valori della pagina hanno la precedenza su quelli da riga di comando
            xlim = (self._num(qs, "xmin", defaults["xmin"]),
                    self._num(qs, "xmax", defaults["xmax"]))
            ylim = (self._num(qs, "ymin", defaults["ymin"]),
                    self._num(qs, "ymax", defaults["ymax"]))
            n = int(self._num(qs, "n", defaults["n"]) or defaults["n"])

            with monitor.lock:
                monitor.refresh()

                if route == "stats.json":
                    return self._send(200, "application/json",
                                      json.dumps(monitor.stats()).encode())

                kinds = {"waveforms.png": "waveforms",
                         "average.png": "average",
                         "amplitudes.png": "amplitudes"}
                if route in kinds:
                    return self._send(200, "image/png",
                                      monitor.figure(kinds[route], n, xlim, ylim))

            self._send(404, "text/plain", b"not found")

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", help="file da monitorare (default: il piu' recente)")
    ap.add_argument("-d", "--data-dir", default="data")
    ap.add_argument("-p", "--port", type=int, default=8765)
    ap.add_argument("-n", "--nevents", type=int, default=1,
                    help="eventi sovrapposti nel grafico (default 1 = solo l'ultimo)")
    ap.add_argument("--xmin", type=float, default=None, help="limite inferiore asse tempi [ns]")
    ap.add_argument("--xmax", type=float, default=None, help="limite superiore asse tempi [ns]")
    ap.add_argument("--ymin", type=float, default=None, help="limite inferiore asse ampiezze [ADC]")
    ap.add_argument("--ymax", type=float, default=None, help="limite superiore asse ampiezze [ADC]")
    ap.add_argument("-m", "--max-events", type=int, default=200,
                    help="eventi piu' recenti usati per le statistiche (default 200)")
    ap.add_argument("-r", "--refresh", type=float, default=5,
                    help="secondi fra un aggiornamento e l'altro (default 5)")
    args = ap.parse_args()

    monitor = Monitor(args.data_dir, args.file, args.max_events,
                      min_interval=max(1.0, args.refresh / 2))
    defaults = {"n": args.nevents, "xmin": args.xmin, "xmax": args.xmax,
                "ymin": args.ymin, "ymax": args.ymax}
    server = ThreadingHTTPServer(("127.0.0.1", args.port),
                                 make_handler(monitor, args.refresh, defaults))

    print(f"Monitor attivo su http://localhost:{args.port}")
    print("In VS Code Remote-SSH la porta viene inoltrata da sola: apri quel")
    print("link nel browser del Mac. Ctrl+C per fermare.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nMonitor fermato.")


if __name__ == "__main__":
    main()
