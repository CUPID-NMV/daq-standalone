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
import errno
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
from daqio import load, baseline_amplitude, DaqFileError


# ----------------------------------------------------------------------
#  Stato condiviso
# ----------------------------------------------------------------------

class Monitor:
    """Tiene l'ultimo set di dati letto e ne calcola rate e statistiche.

    I dati vengono riletti al massimo ogni `min_interval` secondi: il server
    e' multi-thread e senza questa cache ogni immagine richiesta dalla pagina
    farebbe una rilettura completa del file.
    """

    # Range di ingresso del V1742: 1 Vpp di serie, 2 Vpp con l'opzione
    # VPERS1742. L'ADC e' a 12 bit, quindi il passo in mV segue da qui.
    def mv_per_count(self):
        return 1000.0 * self.vpp / 4096.0

    # Il primo percentile su poche centinaia di eventi e' di fatto il secondo
    # valore piu' piccolo e fluttua di +-8 conteggi fra un aggiornamento e
    # l'altro. Serve qualche migliaio di eventi perche' converga.
    MIN_EVENTS_FOR_THRESHOLD = 1000

    def __init__(self, data_dir, path=None, max_events=2000, min_interval=2.0,
                 vpp=1.0):
        self.data_dir = data_dir
        self.fixed_path = path
        self.max_events = max_events
        self.min_interval = min_interval
        self.vpp = vpp

        self.lock = threading.Lock()      # matplotlib non e' thread-safe
        self.hdr = None
        self.data = None
        self.error = None
        self.path = None

        self.n_events = 0
        self.rate = 0.0

        self.status = None        # live-status.json pubblicato dalla DAQ
        self.gen = None           # generazione delle soglie gia' vista
        self.offsets = {}         # offset correnti, per canale
        self.frozen = {}          # ampiezze prima dell'ultimo cambio di soglia
        self.frozen_offsets = {}  # offset a cui si riferiscono
        self._ana = None          # analisi gia' calcolata per questo refresh
        self._last_read = 0.0
        self._prev = None                 # (n_eventi, timestamp)

    # -- lettura -------------------------------------------------------

    def _latest_file(self):
        if self.fixed_path:
            return self.fixed_path
        files = glob.glob(os.path.join(self.data_dir, "*.h5"))
        if not files:
            raise DaqFileError(f"Nessun file .h5 in {os.path.abspath(self.data_dir)}")
        return max(files, key=os.path.getmtime)

    # NOTA: si cercano solo i .h5 non compressi. A run finita il file diventa
    # .h5.gz e non e' piu' monitorabile dal vivo: e' il comportamento voluto,
    # il monitor segue la run in corso.

    def _read_status(self):
        """live-status.json, scritto dalla DAQ. Assente = nessuna informazione."""
        path = os.path.join(self.data_dir, "live-status.json")
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def refresh(self, force=False):
        now = time.time()
        if not force and (now - self._last_read) < self.min_interval:
            return
        self._last_read = now

        try:
            path = self._latest_file()
            # Solo la coda del file: il costo di un aggiornamento non deve
            # crescere con la durata della run.
            hdr, data = load(path, last=self.max_events, live=True)
        except DaqFileError as exc:
            self.error = str(exc).splitlines()[0]
            return
        except Exception as exc:                      # pragma: no cover
            self.error = f"{type(exc).__name__}: {exc}"
            return

        self.error = None
        self.path = path
        total = int(hdr.get("NEventsInFile", data.shape[0]))

        if self._prev is not None:
            prev_n, prev_t = self._prev
            dt = now - prev_t
            if dt > 0 and total >= prev_n:
                self.rate = (total - prev_n) / dt
        self._prev = (total, now)

        self.n_events = total
        self.hdr = hdr
        self.data = data          # gia' limitato alla coda da load(last=...)
        self._ana = None          # ricalcolata sotto, una volta sola

        res = self.analysis()
        if res is None:
            return
        _, _, _, amp, _, _ = res

        st = self._read_status()
        # Un nome vuoto o assente significa "non ancora noto", non "altra run":
        # scartare lo stato lascerebbe in mostra gli offset di una run passata,
        # che e' l'errore peggiore fra i due.
        name = (st or {}).get("file") or ""
        if st and name and name != os.path.basename(path):
            st = None
        self.status = st

        if st is None:
            return

        gen = st.get("generation")
        changed_at = int(st.get("changed_at_event", 0))
        channels = [int(c) for c in hdr["ChannelList"]]

        # Indice assoluto di ogni evento della coda, per sapere quali sono stati
        # acquisiti prima e quali dopo l'ultimo cambio di soglia.
        first = total - data.shape[0]
        is_new = (np.arange(first, total) >= changed_at)

        if gen != self.gen:
            # Congela la distribuzione precedente: senza questo, scorrendo la
            # coda gli eventi vecchi sparirebbero e il confronto con loro.
            if (~is_new).any():
                self.frozen = {ch: amp[~is_new, i].copy()
                               for i, ch in enumerate(channels)}
                self.frozen_offsets = dict(self.offsets)
            self.gen = gen

        self.offsets = {int(c["ch"]): c.get("offset")
                        for c in st.get("channels", [])}
        self._is_new = is_new

    # -- analisi -------------------------------------------------------

    def analysis(self):
        """(hdr, base, corr, amp, t_ns) oppure None se non ci sono ancora dati."""
        if self.data is None or self.data.shape[0] == 0:
            return None
        if self._ana is not None:
            return self._ana

        base, corr, amp, noise = baseline_amplitude(self.data)

        dt_ns = float(self.hdr.get("SamplingTime", 1e-9)) * 1e9
        t_ns = np.arange(self.data.shape[2]) * dt_ns
        self._ana = (self.hdr, base, corr, amp, t_ns, noise)
        return self._ana

    def effective_threshold(self, values, rms):
        """(soglia efficace, avvertimento).

        Ampiezza del piu' piccolo impulso che ha fatto scattare il trigger. La
        soglia impostata e' in conteggi Transparent Mode e non e' confrontabile
        con queste tracce, che sono in Output Mode e su scala diversa; il bordo
        inferiore della distribuzione degli eventi triggerati e' invece la
        soglia vera *in questa* scala. Si usa un percentile basso anziche' il
        minimo, troppo sensibile a un singolo evento.

        Quando la soglia scende sotto il rumore la stima perde significato: la
        maggior parte degli eventi non contiene un impulso e l'"ampiezza"
        diventa la massima escursione del rumore, positiva tanto quanto
        negativa. In quel caso si restituisce un avvertimento invece di un
        numero: e' proprio il sintomo che interessa vedere.
        """
        nz  = max(rms, 0.5)
        sel = np.abs(values) > 5 * nz
        if sel.sum() < self.MIN_EVENTS_FOR_THRESHOLD:
            return None, (f"statistica insufficiente: {sel.sum()} eventi con impulso, "
                          f"ne servono {self.MIN_EVENTS_FOR_THRESHOLD}")

        # In modo "paired" il trigger di un canale fa acquisire anche l'altro,
        # quindi molti eventi senza impulso sono del tutto normali e non
        # indicano affatto che si stia triggerando sul rumore.
        neg = float(np.mean(values[sel] < 0))
        if 0.25 < neg < 0.75:
            return None, "trigger sul rumore: segno delle ampiezze incoerente"

        edge = float(np.percentile(np.abs(values[sel]), 1))
        if edge < 6 * nz:
            return None, "soglia dentro il rumore: le due popolazioni si confondono"

        sign = -1.0 if neg > 0.5 else 1.0
        return sign * edge, None

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

        hdr, base, corr, amp, _, noise = res
        out["shown"] = int(self.data.shape[0])
        out["sampling"] = str(hdr.get("SamplingRate", "?"))
        by_ch = {int(c["ch"]): c for c in (self.status or {}).get("channels", [])}
        for i, ch in enumerate(hdr["ChannelList"]):
            rms = float(np.median(noise[:, i]))
            eff, note = self.effective_threshold(amp[:, i], rms)
            info = by_ch.get(int(ch), {})
            out["channels"].append({
                "ch": int(ch),
                "baseline": round(float(np.mean(base[:, i])), 1),
                "rms": round(rms, 2),
                "amp_mean": round(float(np.mean(amp[:, i])), 1),
                "amp_max": round(float(np.max(np.abs(amp[:, i]))), 1),
                "offset": info.get("offset"),
                "threshold": info.get("threshold"),
                "eff": None if eff is None else round(eff, 1),
                "eff_mv": None if eff is None else round(eff * self.mv_per_count(), 2),
                "eff_note": note,
            })
        return out

    # -- grafici -------------------------------------------------------

    def figure(self, kind, n_show=1, xlim=(None, None), ylim=(None, None),
               hset=None):
        res = self.analysis()
        if res is None:
            return self._placeholder()
        hdr, _, corr, amp, t_ns, noise = res
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

                rms = float(np.median(noise[:, i]))
                eff, note = self.effective_threshold(amp[:, i], rms)
                off = self.offsets.get(int(ch))
                if eff is not None:
                    ax.axhline(eff, color="#d62728", lw=1.1, ls="--",
                               label=f"soglia {eff * self.mv_per_count():.1f} mV"
                                     f"  ({eff:.0f} ADC)"
                                     + (f"  offset {off}" if off is not None else ""))
                    ax.legend(fontsize=8, loc="lower right")
                elif note:
                    # Nessuna riga: disegnarne una qui vorrebbe dire inventarsi
                    # un valore che i dati non sostengono.
                    ax.text(0.99, 0.04, note + (f"  (offset {off})" if off is not None else ""),
                            transform=ax.transAxes, ha="right", va="bottom",
                            fontsize=8, color="#d62728")

                label = ("ultimo evento" if n == 1 else f"ultimi {n} eventi")
                ax.set_title(f"ch{ch} — {label}", fontsize=10)
                ax.set_ylabel("ADC − baseline")
                ax.grid(alpha=0.25)
                apply_limits(ax)
            axes[-1][0].set_xlabel("tempo [ns]")

        elif kind == "average":
            fig, ax = plt.subplots(figsize=(9, 4))
            for i, ch in enumerate(channels):
                line, = ax.plot(t_ns, corr[:, i].mean(axis=0), lw=1.4, label=f"ch{ch}")

                rms = float(np.median(noise[:, i]))
                eff, _ = self.effective_threshold(amp[:, i], rms)
                if eff is not None:
                    ax.axhline(eff, color=line.get_color(), lw=1.0, ls="--", alpha=.7,
                               label=f"soglia ch{ch}: {eff * self.mv_per_count():.1f} mV")
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
                values = amp[:, i]

                # Ogni canale ha i propri limiti e la propria scala
                xlo, xhi, logy = (hset or {}).get(int(ch), (None, None, False))

                # Con un intervallo esplicito i bin vanno calcolati dentro quello,
                # altrimenti si vedrebbe solo una fetta di un istogramma costruito
                # su tutto il range e la risoluzione sarebbe sprecata.
                kw = {}
                if xlo is not None or xhi is not None:
                    lo = xlo if xlo is not None else float(np.min(values))
                    hi = xhi if xhi is not None else float(np.max(values))
                    if hi > lo:
                        kw["range"] = (lo, hi)

                old = self.frozen.get(int(ch))
                is_new = getattr(self, "_is_new", None)
                cur = values[is_new] if (is_new is not None and old is not None
                                         and is_new.shape[0] == values.shape[0]) else values

                # I bin devono essere gli stessi per le due distribuzioni,
                # altrimenti il confronto visivo non significa nulla.
                if "range" not in kw:
                    allv = np.concatenate([cur, old]) if old is not None and old.size else cur
                    kw["range"] = (float(np.min(allv)), float(np.max(allv)))
                # Il numero di bin va sul campione complessivo: subito dopo un
                # cambio di soglia gli eventi nuovi sono pochi e l'istogramma
                # risulterebbe grossolano anche per la parte congelata.
                ntot = cur.size + (old.size if old is not None else 0)
                nb = min(80, max(20, ntot // 8))
                bins = np.linspace(kw["range"][0], kw["range"][1], nb + 1)

                if old is not None and old.size:
                    lo = self.frozen_offsets.get(int(ch))
                    ax.hist(old, bins=bins, color="#888888", alpha=.55,
                            label=f"prima (offset {lo})" if lo is not None else "prima")
                cn = self.offsets.get(int(ch))
                ax.hist(cur, bins=bins, color="#1f77b4", alpha=.85,
                        label=f"ora (offset {cn})" if cn is not None else "ora")
                if old is not None and old.size:
                    ax.legend(fontsize=8)

                ax.set_xlim(*kw["range"])
                if logy:
                    ax.set_yscale("log")

                ax.set_xlabel("ampiezza di picco [ADC]")
                ax.set_ylabel("eventi" + (" (log)" if logy else ""))
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
  #alert { display:none; background:#c0392b; color:#fff; padding:12px 16px;
           border-radius:8px; margin-bottom:16px; font-weight:600; line-height:1.45; }
  #upd { color:var(--mut); font-size:12px; }
  /* Dati fermi: le immagini restano quelle dell'ultimo aggiornamento riuscito,
     quindi vanno smorzate per non farle sembrare aggiornate. */
  body.stale img { opacity:.4; filter:grayscale(.5); }
  .ctl { display:flex; gap:16px; flex-wrap:wrap; align-items:flex-end;
         border:1px solid var(--line); border-radius:8px; padding:12px 16px; margin-bottom:16px; }
  .ctl label { display:flex; flex-direction:column; gap:4px; font-size:12px; color:var(--mut); }
  .ctl input { width:82px; padding:5px 7px; font:13px inherit; color:var(--fg);
               background:var(--bg); border:1px solid var(--line); border-radius:5px; }
  .ctl button { padding:6px 14px; font:13px inherit; border-radius:5px; cursor:pointer;
                border:1px solid var(--line); background:var(--bg); color:var(--fg); }
  .ctl .hint { color:var(--mut); font-size:12px; }
  .ctl label.chk { flex-direction:row; align-items:center; gap:6px; font-size:13px;
                   color:var(--fg); padding-bottom:5px; }
  .ctl label.chk input { width:auto; }
  .ctl .grp { font-weight:600; font-size:13px; padding-bottom:5px; min-width:46px; }
</style></head><body>
<div id="alert"></div>
<h1>DAQ V1742 — monitor online</h1>
<div class="sub"><span id="file">…</span> · <span id="upd">in attesa del primo aggiornamento</span></div>
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
  <span class="hint">forme d'onda · campi vuoti = autoscale</span>
</div>
<div id="hctl"></div>
<table id="tab"><thead><tr><th>canale</th><th>baseline</th><th>rms</th>
<th>ampiezza media</th><th>max</th><th>offset</th><th>soglia</th>
<th>soglia [mV]</th><th>soglia [ADC]</th></tr></thead><tbody></tbody></table>
<div id="boot" class="err">JavaScript non eseguito: la pagina non puo' aggiornarsi.
Apri la console del browser per vedere l'errore.</div>
<img id="w" alt="forme d'onda"><img id="a" alt="media"><img id="h" alt="ampiezze">
<script>
document.getElementById('boot').style.display = 'none';
const REFRESH = __REFRESH__ * 1000;

let lastOk = null;            // ultimo aggiornamento riuscito

function setAlert(msg) {
  const a = document.getElementById('alert');
  a.style.display = msg ? 'block' : 'none';
  if (msg) a.textContent = msg;
  document.body.classList.toggle('stale', !!msg);
}
const FIELDS = ['xmin','xmax','ymin','ymax','nev'];

// I limiti scelti sopravvivono a un reload della pagina. localStorage puo'
// essere inaccessibile (finestra privata, cookie bloccati): mai fatale.
try {
  for (const f of FIELDS) {
    const v = localStorage.getItem('daqmon.' + f);
    if (v !== null) document.getElementById(f).value = v;
  }
} catch (e) {}

// I controlli dell'istogramma sono uno per canale e la lista dei canali si
// conosce solo da stats.json, quindi vengono costruiti al primo giro e
// ricostruiti solo se i canali cambiano: rifarli a ogni aggiornamento
// cancellerebbe quello che si sta digitando.
let builtChannels = null;

function store(k, v) { try { localStorage.setItem('daqmon.' + k, v); } catch (e) {} }
function recall(k)   { try { return localStorage.getItem('daqmon.' + k); } catch (e) { return null; } }

function buildHistControls(channels) {
  const key = channels.join(',');
  if (key === builtChannels) return;
  builtChannels = key;

  document.getElementById('hctl').innerHTML = channels.map(ch => `
    <div class="ctl">
      <span class="grp">ch${ch}</span>
      <label>istogramma x min [ADC]<input id="hxmin_${ch}" placeholder="auto"></label>
      <label>istogramma x max [ADC]<input id="hxmax_${ch}" placeholder="auto"></label>
      <label class="chk"><input type="checkbox" id="hlog_${ch}"> log y</label>
      <button class="hreset" data-ch="${ch}">Autoscale</button>
    </div>`).join('');

  for (const ch of channels) {
    for (const k of ['hxmin_' + ch, 'hxmax_' + ch]) {
      const v = recall(k);
      if (v !== null) document.getElementById(k).value = v;
      document.getElementById(k).addEventListener('change', tick);
    }
    const lg = document.getElementById('hlog_' + ch);
    if (recall('hlog_' + ch) !== null) lg.checked = (recall('hlog_' + ch) === '1');
    lg.addEventListener('change', tick);
  }

  document.querySelectorAll('#hctl button.hreset').forEach(b => b.onclick = () => {
    const ch = b.dataset.ch;
    document.getElementById('hxmin_' + ch).value = '';
    document.getElementById('hxmax_' + ch).value = '';
    document.getElementById('hlog_' + ch).checked = false;
    tick();
  });
}

function params() {
  const p = new URLSearchParams();
  for (const f of FIELDS) {
    const v = document.getElementById(f).value.trim();
    try { localStorage.setItem('daqmon.' + f, v); } catch (e) {}
    if (v !== '') p.set(f === 'nev' ? 'n' : f, v);
  }
  if (builtChannels) for (const ch of builtChannels.split(',')) {
    for (const k of ['hxmin_' + ch, 'hxmax_' + ch]) {
      const v = document.getElementById(k).value.trim();
      store(k, v);
      if (v !== '') p.set(k, v);
    }
    const on = document.getElementById('hlog_' + ch).checked;
    store('hlog_' + ch, on ? '1' : '0');
    p.set('hlog_' + ch, on ? '1' : '0');
  }
  return p;
}

document.getElementById('reset').onclick = () => {
  for (const f of ['xmin','xmax','ymin','ymax']) document.getElementById(f).value = '';
  tick();
};
for (const f of FIELDS)
  document.getElementById(f).addEventListener('change', tick);
function show(id, v) {
  document.getElementById(id).textContent = (v === null || v === undefined) ? '-' : v;
}
async function tick() {
  try {
    const r = await fetch('stats.json', {cache:'no-store'});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const s = await r.json();
    lastOk = new Date();
    setAlert(null);
    document.getElementById('upd').textContent =
      'aggiornato alle ' + lastOk.toLocaleTimeString();
    show('rate', s.rate); show('events', s.events); show('shown', s.shown);
    document.getElementById('err').textContent    = s.error || '';
    document.getElementById('file').textContent   =
      (s.file || 'nessun file') + (s.sampling ? ' · ' + s.sampling : '');
    buildHistControls((s.channels || []).map(c => c.ch));
    const tb = document.querySelector('#tab tbody');
    const na = v => (v === null || v === undefined) ? '-' : v;
    tb.innerHTML = (s.channels||[]).map(c =>
      `<tr><td>ch${c.ch}</td><td>${c.baseline}</td><td>${c.rms}</td>
       <td>${c.amp_mean}</td><td>${c.amp_max}</td>
       <td>${na(c.offset)}</td><td>${na(c.threshold)}</td>
       <td>${c.eff_note ? '<span class="err">'+c.eff_note+'</span>' : na(c.eff_mv)}</td>
       <td>${na(c.eff)}</td></tr>`).join('');
    const p = params();
    p.set('t', Date.now());
    for (const [id, name] of [['w','waveforms'],['a','average'],['h','amplitudes']])
      document.getElementById(id).src = name + '.png?' + p.toString();
  } catch (e) {
    // Le immagini restano quelle di prima: senza un avviso vistoso la pagina
    // sembrerebbe viva mentre mostra dati fermi.
    const why = (e && e.message) ? e.message : String(e);
    const msg = lastOk
      ? `Server non raggiungibile (${why}). Dati fermi all'ultimo aggiornamento `
        + `riuscito: ${lastOk.toLocaleTimeString()}, `
        + `${Math.round((Date.now() - lastOk.getTime()) / 1000)} s fa. `
        + `Il monitor sulla macchina DAQ e' probabilmente stato chiuso.`
      : `Server non raggiungibile (${why}). Nessun dato ricevuto da quando la `
        + `pagina e' stata aperta: controlla che il monitor sia in esecuzione.`;
    setAlert(msg);
    document.getElementById('err').textContent = '';
  }
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

            if route == "favicon.ico":
                # Il browser la chiede da solo: senza questa riga la console
                # si riempie di 404 e nasconde gli errori veri.
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            if route == "index.html":
                page = (PAGE.replace("__REFRESH__", str(refresh))
                            .replace("__NEVENTS__", str(defaults["n"]))
                            .replace("__XMIN__", _fmt(defaults["xmin"]))
                            .replace("__XMAX__", _fmt(defaults["xmax"]))
                            .replace("__YMIN__", _fmt(defaults["ymin"]))
                            .replace("__YMAX__", _fmt(defaults["ymax"]))
)
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
                    # I limiti dell'istogramma sono per canale: hxmin_8, hlog_9, ...
                    # I valori da riga di comando fanno da default per tutti.
                    hset = {}
                    for ch in (monitor.hdr or {}).get("ChannelList", []):
                        ch = int(ch)
                        hset[ch] = (
                            self._num(qs, f"hxmin_{ch}", defaults["hxmin"]),
                            self._num(qs, f"hxmax_{ch}", defaults["hxmax"]),
                            qs.get(f"hlog_{ch}",
                                   ["1" if defaults["hlog"] else "0"])[0] == "1",
                        )
                    return self._send(200, "image/png",
                                      monitor.figure(kinds[route], n, xlim, ylim, hset))

            self._send(404, "text/plain", b"not found")

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", help="file da monitorare (default: il piu' recente)")
    ap.add_argument("-d", "--data-dir", default=None,
                    help="directory dei dati (default: <radice del progetto>/data)")
    ap.add_argument("-p", "--port", type=int, default=8765)
    ap.add_argument("-n", "--nevents", type=int, default=1,
                    help="eventi sovrapposti nel grafico (default 1 = solo l'ultimo)")
    ap.add_argument("--xmin", type=float, default=None, help="limite inferiore asse tempi [ns]")
    ap.add_argument("--xmax", type=float, default=None, help="limite superiore asse tempi [ns]")
    ap.add_argument("--ymin", type=float, default=None, help="limite inferiore asse ampiezze [ADC]")
    ap.add_argument("--ymax", type=float, default=None, help="limite superiore asse ampiezze [ADC]")
    ap.add_argument("--hxmin", type=float, default=None,
                    help="limite inferiore dell'istogramma delle ampiezze [ADC]")
    ap.add_argument("--hxmax", type=float, default=None,
                    help="limite superiore dell'istogramma delle ampiezze [ADC]")
    ap.add_argument("--hlog", action="store_true",
                    help="asse y logaritmico nell'istogramma delle ampiezze")
    ap.add_argument("-m", "--max-events", type=int, default=2000,
                    help="eventi piu' recenti usati per le statistiche (default 2000; "
                         "sotto il migliaio la stima della soglia non e' stabile)")
    ap.add_argument("--vpp", type=float, default=1.0,
                    help="range di ingresso del digitizer in Vpp (default 1.0; "
                         "2.0 per la versione VPERS1742)")
    ap.add_argument("-r", "--refresh", type=float, default=5,
                    help="secondi fra un aggiornamento e l'altro (default 5)")
    args = ap.parse_args()

    # Il default va ancorato alla radice del progetto, non alla directory
    # corrente: lanciando lo script da tools/ un "data" relativo punterebbe a
    # tools/data, che non esiste, e il monitor resterebbe vuoto senza spiegare
    # perche'.
    data_dir = args.data_dir
    if data_dir is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(root, "data")

    if not os.path.isdir(data_dir):
        sys.exit(f"La directory dei dati non esiste: {os.path.abspath(data_dir)}\n"
                 "Indicane un'altra con -d.")

    print(f"Dati letti da: {os.path.abspath(data_dir)}")

    monitor = Monitor(data_dir, args.file, args.max_events,
                      min_interval=max(1.0, args.refresh / 2), vpp=args.vpp)
    defaults = {"n": args.nevents, "xmin": args.xmin, "xmax": args.xmax,
                "ymin": args.ymin, "ymax": args.ymax,
                "hxmin": args.hxmin, "hxmax": args.hxmax, "hlog": args.hlog}

    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port),
                                     make_handler(monitor, args.refresh, defaults))
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        # Caso tipico: un'altra istanza del monitor e' gia' attiva, magari
        # avviata da qualcun altro o lasciata in background.
        sys.exit(
            f"La porta {args.port} e' gia' in uso.\n\n"
            "Di solito significa che un monitor e' gia' attivo: prova ad aprire\n"
            f"  http://localhost:{args.port}\n"
            "prima di lanciarne un altro.\n\n"
            "Per vedere chi la occupa:   ss -ltnp | grep " + str(args.port) + "\n"
            f"Per usare un'altra porta:   {os.path.basename(sys.argv[0])} -p {args.port + 1}"
        )

    print(f"Monitor attivo su http://localhost:{args.port}")
    print("In VS Code Remote-SSH la porta viene inoltrata da sola: apri quel")
    print("link nel browser del Mac. Ctrl+C per fermare.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nMonitor fermato.")


if __name__ == "__main__":
    main()
