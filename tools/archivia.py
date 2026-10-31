#!/usr/bin/env python3
"""Compressione e trasferimento sul cloud delle run finite, fuori dalla DAQ.

    python3 tools/archivia.py [CARTELLA ...]        un giro su quello che e' pronto
    python3 tools/archivia.py --stato               cosa e' fatto, cosa aspetta, cosa e' fallito
    python3 tools/archivia.py --aggiungi FILE ...   manda sul cloud run gia' presenti
    python3 tools/archivia.py --comprimi FILE ...   comprime file di prima dell'attivazione
    python3 tools/archivia.py --senza-cloud         comprime soltanto

Fino al 2026-10-31 il file lo comprimeva la DAQ a fine run, con gzip, prima
di uscire: ~55 s su una run da 1 GB (0041-0054), e per quel tempo la coda
restava ferma. Con CompressAtEnd = false nel TOML la DAQ chiude il file ed
esce; questo script lo comprime e lo manda sul cloud mentre la run dopo e'
gia' partita. Lo lancia il controllore in background a fine run, al suo avvio
e ogni dieci minuti: un giro saltato (controllore spento, PC riavviato, rete
giu') si recupera al giro dopo. Guarda OutputDir e OutputDir/scan del TOML
piu' ogni cartella che gli sia mai stata indicata.

Compressione e trasferimento hanno un lucchetto ciascuno: un trasferimento
lento non ferma la compressione delle run che finiscono nel frattempo. Un file
alla volta, a priorita' minima per processore e disco.

Per ogni run:
  1. aspetta due minuti dalla fine della DAQ, cosi' chi legge il file a fine
     run (la stima dell'efficienza del controllore, lo scan del V812) lo trova
     intero e non compresso;
  2. comprime con pigz in X.h5.gz.part, verifica il gzip e la lunghezza,
     rinomina in X.h5.gz e solo allora cancella X.h5: nessuno strumento vede
     mai un .gz scritto a meta'. Il .gz tiene la data del .h5, come faceva
     gzip: la cache dell'efficienza e i riepiloghi ci contano;
  3. con CloudUpload = true (sezione [archive] del TOML) manda con cp_data il
     .h5.gz, l'eventuale .mid.gz e per ultimo il .log: se sul cloud c'e' il
     .log, c'e' anche il resto. Se fallisce riprova dopo 5 minuti, poi 15,
     45, poco piu' di due ore, poi ogni 6 ore.

Cosa non tocca, di proposito:
  - i file CHIUSI MALE. Un HDF5 scritto in SWMR porta nel superblocco un flag
    "aperto in scrittura" che la chiusura regolare azzera: se e' ancora alzato
    la run e' morta (timeout di uno scan, link perso, kill) e il file si
    rilegge solo in modalita' live, che su un .gz daqio rifiuta. La DAQ non li
    comprimeva nemmeno prima. Verificato il 2026-10-31: i quattro .h5 rimasti
    non compressi sul disco hanno il flag a 5, quelli chiusi bene a 0;
  - i file di prima dell'attivazione (la prima riga del registro): qualcuno
    puo' averli lasciati non compressi apposta. Per quelli --comprimi e
    --aggiungi;
  - le run di prova TEST_, che la DAQ cancella da se';
  - sul cloud, le cartelle che si chiamano scan. cp_data mette tutto in una
    sola cartella col solo nome del file, e S3 sovrascrive senza avvisare:
    sul disco lo stesso nome compare in piu' cartelle (43 nomi in data/, 3
    dentro data/LNGS, al 2026-10-31). Per la stessa ragione un nome gia'
    mandato da un altro file non si rimanda: si segnala come conflitto.

Registro: data/archivio.jsonl, una riga per azione. E' la memoria dello
script: cosa e' gia' sul cloud, quanti tentativi ha fatto un file, da quando
e' attivo. Il controllore ne scrive l'uscita in data/archivio.log.
"""

import argparse
import collections
import fcntl
import glob
import hashlib
import json
import os
import shutil
import struct
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOML = os.path.join(ROOT, "config", "run-local.toml")
REGISTRO = os.path.join(ROOT, "data", "archivio.jsonl")
ATTESA_S = 120
RIPROVA_S = (300, 900, 2700, 8100, 21600)
# cp_data chiede credenziali che valgono un'ora: oltre, un trasferimento non
# puo' piu' riuscire, e tenerlo vivo terrebbe solo occupato il lucchetto.
TIMEOUT_CARICA_S = 3900
FIRMA_HDF5 = b"\x89HDF\r\n\x1a\n"
NICE = ["nice", "-n", "19", "ionice", "-c", "3"]
# pigz scrive lo stesso formato di gzip: daqio e i notebook non vedono
# differenze. Quattro dei sedici core, per non contendere con la lettura.
GZIP = ["pigz", "-p", "4"] if shutil.which("pigz") else ["gzip"]
FORMATO_T = "%Y-%m-%d %H:%M:%S"


def adesso():
    return time.strftime(FORMATO_T)


def epoca(t):
    return time.mktime(time.strptime(t, FORMATO_T))


def ora(s):
    return time.strftime("%d/%m %H:%M", time.localtime(s))


def scrivi(testo):
    print("%s  %s" % (adesso(), testo), flush=True)


def mb(n):
    return "%.0f MB" % (n / 1e6)


def data_di(path):
    """La data del file, o 0 se intanto e' sparito (una TEST_ che la DAQ cancella)."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def base_run(path):
    for suff in (".h5.gz", ".h5"):
        if path.endswith(suff):
            return path[:-len(suff)]
    return path


def md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for blocco in iter(lambda: f.read(1 << 22), b""):
            h.update(blocco)
    return h.hexdigest()


class Registro:
    """Il registro, in sola aggiunta. Si rilegge a ogni giro: quello che un
    altro esemplare (o un --aggiungi da terminale) scrive nel frattempo ci
    finisce dentro. Indicizzato per tipo, file, chiave e origine: in un anno
    sono decine di migliaia di righe, e lo si interroga per ogni run."""

    CAMPI = ("file", "chiave", "origine")

    def __init__(self, path):
        self.path = path
        self.rileggi()
        if not self.di("attivato"):
            self.aggiungi("attivato")

    def rileggi(self):
        self.eventi, self._indice = [], collections.defaultdict(list)
        try:
            with open(self.path) as f:
                for riga in f:
                    try:
                        self._metti(json.loads(riga))
                    except ValueError:
                        pass            # una riga troncata da un arresto brusco
        except FileNotFoundError:
            pass

    def _metti(self, e):
        self.eventi.append(e)
        self._indice[e.get("evento")].append(e)
        for k in self.CAMPI:
            if k in e:
                self._indice[(k, e[k])].append(e)

    def aggiungi(self, evento, **campi):
        e = dict(t=adesso(), evento=evento, **campi)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
        self._metti(e)
        return e

    def di(self, evento, **campi):
        lista = self._indice.get(evento, [])
        for k in self.CAMPI:
            if k in campi:
                lista = self._indice.get((k, campi[k]), [])
                break
        return [e for e in lista if e.get("evento") == evento
                and all(e.get(k) == v for k, v in campi.items())]

    def attivato(self):
        return epoca(self.di("attivato")[0]["t"])

    def file_di(self, evento):
        return list(dict.fromkeys(e["file"] for e in self.di(evento)))

    def tentativi(self, path):
        """(errori dall'ultimo trasferimento riuscito, istante dell'ultimo)."""
        n, ultimo = 0, None
        for e in self._indice.get(("file", path), []):
            if e["evento"] == "caricato":
                n, ultimo = 0, None
            elif e["evento"] == "errore_carica":
                n, ultimo = n + 1, epoca(e["t"])
        return n, ultimo

    def una_volta(self, evento, path, testo, **campi):
        """Una situazione che si ripresenta a ogni giro si scrive una volta sola."""
        if not self.di(evento, file=path):
            self.aggiungi(evento, file=path, **campi)
            scrivi(testo)


def lucchetto(path):
    """Il lucchetto, oppure None se lo tiene gia' un altro esemplare."""
    f = open(path, "a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except BlockingIOError:
        f.close()
        return None


def leggi_toml(path):
    import tomllib
    with open(path, "rb") as f:
        return tomllib.load(f)


def cartelle_da_guardare(cfg, reg, extra):
    od = cfg.get("digitizer", {}).get("OutputDir")
    cartelle = []
    for d in [str(od), os.path.join(str(od), "scan")] if od else []:
        d = os.path.realpath(d)
        if os.path.isdir(d):
            cartelle.append(d)
    for d in extra:
        d = os.path.realpath(d)
        if os.path.isdir(d) and not reg.di("cartella", file=d):
            reg.aggiungi("cartella", file=d)
    # Le cartelle indicate restano nel giro anche dopo: una run scritta fuori
    # da OutputDir non va persa se il suo invito arriva mentre un altro
    # esemplare ha il lucchetto.
    for d in reg.file_di("cartella"):
        if os.path.isdir(d) and d not in cartelle:
            cartelle.append(d)
    return cartelle


def aperti_dalla_daq():
    """I file che un DAQ-WC vivo tiene aperti: quelli non si toccano."""
    aperti = set()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/comm" % pid) as f:
                if f.read().strip() != "DAQ-WC":
                    continue
            for fd in os.listdir("/proc/%s/fd" % pid):
                try:
                    aperti.add(os.path.realpath(os.readlink("/proc/%s/fd/%s" % (pid, fd))))
                except OSError:
                    pass
        except OSError:
            pass
    return aperti


def chiusura(h5):
    """'bene', 'male', 'senza flag' (superblocco 0 o 1) oppure 'non HDF5'.

    Superblocco 2 e 3: firma (8 byte), versione, dimensione degli offset e
    delle lunghezze, poi i flag di consistenza. La chiusura regolare li
    azzera; bit 0 = aperto in scrittura, bit 2 = aperto per SWMR.
    """
    with open(h5, "rb") as f:
        testa = f.read(12)
    if len(testa) < 12 or testa[:8] != FIRMA_HDF5:
        return "non HDF5"
    if testa[8] < 2:
        return "senza flag"
    return "bene" if testa[11] == 0 else "male"


def classifica(h5, reg, aperti, forzati, attesa_s):
    """Cosa fare di un .h5, senza farlo: (etichetta, secondi che mancano)."""
    if os.path.basename(h5).startswith("TEST_"):
        return "run di prova", 0
    log = base_run(h5) + ".log"
    if h5 in aperti or log in aperti:
        return "in corso", 0
    mtime = os.path.getmtime(h5)
    if mtime < reg.attivato() and h5 not in forzati:
        return "prima dell'attivazione", 0
    stato = chiusura(h5)
    if stato in ("male", "non HDF5"):
        return "chiusa male" if stato == "male" else "non HDF5", 0
    if os.path.exists(h5 + ".gz"):
        mio = reg.di("compresso", origine=h5, byte_h5=os.path.getsize(h5), mtime_h5=mtime)
        return ("avanzo" if mio else "doppio"), 0
    fine = max(mtime, data_di(log))
    resta = fine + attesa_s - time.time()
    return ("in attesa", resta) if resta > 0 else ("pronto", 0)


def h5_da_guardare(cartelle, forzati):
    candidati = set(f for f in forzati if os.path.exists(f))
    for d in cartelle:
        candidati |= {os.path.realpath(f) for f in glob.glob(os.path.join(d, "*.h5"))}
    return sorted(candidati, key=data_di)


def da_comprimere(cartelle, reg, forzati, attesa_s, scartati):
    """I .h5 pronti, e fra quanti secondi lo sara' il primo che ancora non lo e'."""
    aperti = aperti_dalla_daq()
    pronti, aspetta = [], None
    for h5 in h5_da_guardare(cartelle, forzati):
        if h5 in scartati:
            continue
        try:
            etichetta, resta = classifica(h5, reg, aperti, forzati, attesa_s)
        except OSError:
            continue                    # sparito o illeggibile: al prossimo giro
        if etichetta == "pronto":
            pronti.append(h5)
        elif etichetta == "in attesa":
            aspetta = resta if aspetta is None else min(aspetta, resta)
        elif etichetta == "chiusa male":
            reg.una_volta("chiusa_male", h5, "chiusa male, lasciata .h5 com'era: %s" % h5)
        elif etichetta == "non HDF5":
            reg.una_volta("non_hdf5", h5, "non e' un HDF5, non lo tocco: %s" % h5)
        elif etichetta == "doppio":
            reg.una_volta("doppio", h5, "c'e' gia' %s.gz non fatto da me: il .h5 resta" % h5)
        elif etichetta == "avanzo":
            # Arresto fra la rinomina e la cancellazione: il .gz e' gia'
            # verificato e registrato, il .h5 e' quello che resta.
            os.unlink(h5)
            scrivi("tolto il .h5 rimasto accanto al suo .gz: %s" % h5)
    return pronti, aspetta


def comprimi(h5, reg):
    gz, part = h5 + ".gz", h5 + ".gz.part"
    st = os.stat(h5)
    libero = shutil.disk_usage(os.path.dirname(h5)).free
    if libero < 0.6 * st.st_size + 2e9:
        raise RuntimeError("disco quasi pieno: %.1f GB liberi" % (libero / 1e9))
    scrivi("comprimo %s (%s)" % (h5, mb(st.st_size)))
    t0 = time.time()
    try:
        with open(part, "wb") as out:
            r = subprocess.run(NICE + GZIP + ["-6", "-c", h5], stdout=out, stderr=subprocess.PIPE)
        if r.returncode:
            raise RuntimeError("%s: %s" % (GZIP[0], r.stderr.decode(errors="replace").strip()[-300:]))
        # Verifica dallo standard input: un nome che non finisce in .gz
        # potrebbe essere rifiutato per il suffisso, non per il contenuto.
        with open(part, "rb") as dentro:
            r = subprocess.run(NICE + GZIP + ["-t"], stdin=dentro, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE)
        if r.returncode:
            raise RuntimeError("il .gz non passa la verifica: %s"
                               % r.stderr.decode(errors="replace").strip()[-300:])
        # La lunghezza originale sta negli ultimi 4 byte, modulo 2^32: il .gz
        # deve contenere il file intero, non quello che c'era a un certo punto.
        with open(part, "rb") as f:
            f.seek(-4, os.SEEK_END)
            if struct.unpack("<I", f.read(4))[0] != st.st_size % (1 << 32):
                raise RuntimeError("la lunghezza decompressa non e' quella del .h5")
        shutil.copymode(h5, part)
        os.utime(part, ns=(st.st_atime_ns, st.st_mtime_ns))
        md5 = md5_file(part)
        os.replace(part, gz)
    except BaseException:
        if os.path.exists(part):
            os.unlink(part)
        raise
    dt = time.time() - t0
    n = os.path.getsize(gz)
    reg.aggiungi("compresso", file=gz, origine=h5, byte_h5=st.st_size, mtime_h5=st.st_mtime,
                 byte=n, md5=md5, secondi=round(dt, 1))
    os.unlink(h5)
    scrivi("compresso in %.0f s: %s -> %s" % (dt, mb(st.st_size), mb(n)))


def pulisci_part(cartelle):
    """Avanzi di una compressione interrotta: col lucchetto in mano, nessuno li sta scrivendo."""
    for d in cartelle:
        for p in glob.glob(os.path.join(d, "*.h5.gz.part")):
            os.unlink(p)
            scrivi("tolto l'avanzo di una compressione interrotta: %s" % p)


def da_caricare(cartelle, reg):
    """Le run da mandare, ciascuna come lista di file nell'ordine in cui mandarli."""
    attivato = reg.attivato()
    dati = set()
    for d in cartelle:
        if "scan" in d.split(os.sep):
            continue
        for gz in glob.glob(os.path.join(d, "*.h5.gz")):
            if not os.path.basename(gz).startswith("TEST_") and data_di(gz) >= attivato:
                dati.add(os.path.realpath(gz))
    for f in reg.file_di("aggiunto"):
        if not os.path.exists(f) and os.path.exists(f + ".gz"):
            f += ".gz"                      # aggiunto come .h5, compresso dopo
        if os.path.exists(f):
            dati.add(f)
    runs = []
    for f in sorted(dati, key=data_di):
        b = base_run(f)
        runs.append([f] + [p for p in (b + ".mid.gz", b + ".log") if os.path.exists(p)])
    return runs


def gia_caricato(path, reg):
    st = os.stat(path)
    return bool(reg.di("caricato", file=path, byte=st.st_size, mtime=st.st_mtime))


def ostacolo(path, reg):
    """Perche' un file non si puo' mandare adesso: (motivo, spiegazione), oppure None."""
    chiave = os.path.basename(path)
    altri = [e for e in reg.di("caricato", chiave=chiave) if e["file"] != path]
    if altri:
        return "conflitto", "col nome %s c'e' gia' sul cloud %s" % (chiave, altri[-1]["file"])
    if reg.di("caricato", file=path):
        return "cambiato", "e' cambiato dopo il trasferimento"
    n, ultimo = reg.tentativi(path)
    if n:
        dopo = ultimo + RIPROVA_S[min(n, len(RIPROVA_S)) - 1]
        if time.time() < dopo:
            return "in pausa", "%d tentativi falliti, il prossimo dopo le %s" % (n, ora(dopo))
    return None


def carica_run(parti, comando, reg):
    """Manda i file di una run nell'ordine, fermandosi al primo che non passa.
    Torna True se ha provato almeno un trasferimento."""
    provato = False
    for p in parti:
        if gia_caricato(p, reg):
            continue
        perche = ostacolo(p, reg)
        if perche:
            if perche[0] in ("conflitto", "cambiato"):
                reg.una_volta(perche[0], p, "NON lo mando, %s: %s" % (perche[1], p))
            return provato
        st = os.stat(p)
        md5 = md5_file(p)
        scrivi("mando sul cloud %s (%s)" % (p, mb(st.st_size)))
        provato = True
        t0 = time.time()
        try:
            r = subprocess.run(NICE + [comando, p], capture_output=True, text=True,
                               timeout=TIMEOUT_CARICA_S)
            ok = r.returncode == 0
            righe = (r.stdout if ok else r.stderr or r.stdout).strip().splitlines()
            messaggio = righe[-1] if righe else "codice d'uscita %d" % r.returncode
        except subprocess.TimeoutExpired:
            ok, messaggio = False, "non finito in %d minuti" % (TIMEOUT_CARICA_S // 60)
        except OSError as e:
            ok, messaggio = False, str(e)
        dt = max(time.time() - t0, 1e-3)
        if not ok:
            reg.aggiungi("errore_carica", file=p, chiave=os.path.basename(p),
                         messaggio=messaggio[-300:], secondi=round(dt, 1))
            scrivi("trasferimento fallito (tentativo %d): %s" % (reg.tentativi(p)[0], messaggio))
            return provato
        reg.aggiungi("caricato", file=p, chiave=os.path.basename(p), byte=st.st_size,
                     mtime=st.st_mtime, md5=md5, secondi=round(dt, 1),
                     mb_s=round(st.st_size / 1e6 / dt, 2), uscita=messaggio[-300:])
        scrivi("sul cloud in %.0f s (%.1f MB/s): %s" % (dt, st.st_size / 1e6 / dt,
                                                        os.path.basename(p)))
    return provato


def forzati_di(reg, extra=()):
    f = set(extra) | set(reg.file_di("da_comprimere"))
    return f | {x for x in reg.file_di("aggiunto") if x.endswith(".h5")}


def giro_compressione(cartelle, reg, attesa_s):
    scartati = set()        # compressioni fallite: si riprova al prossimo giro, non in questo
    pulisci_part(cartelle)
    while True:
        reg.rileggi()
        pronti, aspetta = da_comprimere(cartelle, reg, forzati_di(reg), attesa_s, scartati)
        for h5 in pronti:
            try:
                comprimi(h5, reg)
            except Exception as e:
                scartati.add(h5)
                reg.aggiungi("errore_compressione", file=h5, messaggio=str(e)[-300:])
                scrivi("compressione fallita: %s: %s" % (h5, e))
        if pronti:
            continue            # nel frattempo puo' essere finita un'altra run
        if aspetta is None:
            return
        time.sleep(min(aspetta, attesa_s) + 1)


def giro_cloud(cartelle, reg, comando):
    while True:
        reg.rileggi()
        provato = False
        for parti in da_caricare(cartelle, reg):
            provato |= carica_run(parti, comando, reg)
        if not provato:
            return


def mostra_stato(reg, cartelle, cloud, comando, attesa_s, occupati):
    print("Registro    : %s, attivo dal %s" % (reg.path, ora(reg.attivato())))
    print("Cloud       : %s" % ("acceso, con %s" % comando if cloud
                                else "spento ([archive] CloudUpload nel TOML)"))
    print("Al lavoro   : %s" % (", ".join(occupati) or "nessuno"))
    print("Cartelle    : %s" % "\n              ".join(cartelle))

    aperti, forzati = aperti_dalla_daq(), forzati_di(reg)
    conta, righe = collections.Counter(), []
    for h5 in h5_da_guardare(cartelle, forzati):
        try:
            etichetta, resta = classifica(h5, reg, aperti, forzati, attesa_s)
        except OSError:
            continue
        conta[etichetta] += 1
        if etichetta in ("in corso", "in attesa", "pronto", "doppio", "avanzo"):
            righe.append("  %-10s %s%s" % (etichetta, h5, " (fra %.0f s)" % resta if resta else ""))
    print("\nFile .h5    : %s" % (", ".join("%s %d" % kv for kv in sorted(conta.items()))
                                  or "nessuno"))
    if righe:
        print("\n".join(righe))

    print("\nRun da mandare o mandate (le ultime 15):")
    for parti in da_caricare(cartelle, reg)[-15:]:
        fatti = [p for p in parti if gia_caricato(p, reg)]
        if len(fatti) == len(parti):
            e = reg.di("caricato", file=parti[0])[-1]
            stato = "sul cloud dal %s (%s in %.0f s)" % (e["t"], mb(e["byte"]), e["secondi"])
        else:
            manca = [p for p in parti if p not in fatti][0]
            perche = ostacolo(manca, reg)
            stato = ("%s: %s" % perche) if perche else "da mandare"
            if reg.tentativi(manca)[0]:
                stato += "; ultimo errore: %s" % reg.di("errore_carica", file=manca)[-1]["messaggio"]
        print("  %-40s %s" % (os.path.basename(base_run(parti[0])), stato))

    print("\nUltime azioni:")
    for e in [e for e in reg.eventi if e["evento"] not in ("attivato", "cartella")][-10:]:
        print("  %s  %-19s %s" % (e["t"], e["evento"], e.get("file", "")))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cartelle", nargs="*",
                    help="cartelle da guardare oltre a OutputDir e OutputDir/scan")
    ap.add_argument("--toml", default=TOML)
    ap.add_argument("--registro", default=REGISTRO,
                    help="una prova deve usarne uno suo, se no agisce sui dati veri")
    ap.add_argument("--stato", action="store_true")
    ap.add_argument("--aggiungi", nargs="+", metavar="FILE", default=[],
                    help="file dei dati (.h5 o .h5.gz) da mandare sul cloud con il loro .log")
    ap.add_argument("--comprimi", nargs="+", metavar="FILE", default=[],
                    help=".h5 da comprimere anche se precedenti all'attivazione")
    ap.add_argument("--senza-cloud", action="store_true")
    ap.add_argument("--attesa", type=float, default=ATTESA_S, help=argparse.SUPPRESS)
    a = ap.parse_args()

    cfg = leggi_toml(a.toml)
    arch = cfg.get("archive", {})
    cloud = bool(arch.get("CloudUpload", False)) and not a.senza_cloud
    comando = str(arch.get("CloudCommand", "/usr/bin/cp_data"))
    reg = Registro(os.path.abspath(a.registro))
    for opzione, evento, suff in ((a.aggiungi, "aggiunto", (".h5", ".h5.gz")),
                                  (a.comprimi, "da_comprimere", (".h5",))):
        for f in opzione:
            f = os.path.realpath(f)
            if not os.path.exists(f) or not f.endswith(suff):
                ap.error("non esiste o non e' un %s: %s" % (" / ".join(suff), f))
            if f not in reg.file_di(evento):
                reg.aggiungi(evento, file=f)
    cartelle = cartelle_da_guardare(cfg, reg, a.cartelle)

    if a.stato:
        occupati = []
        for nome, quale in (("compressione", ".comprimo.lock"), ("trasferimento", ".cloud.lock")):
            f = lucchetto(reg.path + quale)
            if f is None:
                occupati.append(nome)
            else:
                f.close()
        mostra_stato(reg, cartelle, cloud, comando, a.attesa, occupati)
        return
    if cloud and not os.access(comando, os.X_OK):
        scrivi("CloudUpload = true ma %s non c'e' o non e' eseguibile: comprimo soltanto" % comando)
        cloud = False

    # Prima si comprime tutto il pronto, poi si trasferisce. Il lucchetto della
    # compressione si lascia prima di trasferire: se un trasferimento dura, il
    # prossimo esemplare lanciato dal controllore comprime lo stesso.
    f = lucchetto(reg.path + ".comprimo.lock")
    if f is not None:
        try:
            giro_compressione(cartelle, reg, a.attesa)
        finally:
            f.close()
    if cloud:
        f = lucchetto(reg.path + ".cloud.lock")
        if f is not None:
            try:
                giro_cloud(cartelle, reg, comando)
            finally:
                f.close()


if __name__ == "__main__":
    main()
