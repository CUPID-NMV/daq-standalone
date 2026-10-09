#!/usr/bin/env python3
"""Controllo remoto dell'alimentatore Aim-TTi PLH250-P (250 V, 375 mA).

Parla il protocollo testuale TTi su TCP porta 9221: un comando per riga,
le query finiscono con '?'. Funziona da qualunque macchina che veda la
sottorete del PC DAQ.

    python3 tools/psu_control.py status
    python3 tools/psu_control.py set 54.0            # rampa fino a 54 V
    python3 tools/psu_control.py set 54.0 --step 2 --dwell 0.5
    python3 tools/psu_control.py ilim 0.0002         # limite di corrente, A
    python3 tools/psu_control.py on | off
    python3 tools/psu_control.py ovp 60 | ocp 0.001
    python3 tools/psu_control.py monitor --every 1 --csv hv.csv
    python3 tools/psu_control.py find                # cerca lo strumento
    python3 tools/psu_control.py raw "V1?"

L'indirizzo lo assegna il DHCP e cambia (come e' gia' successo al PC DAQ):
se quello di serie non risponde, `find` lo ritrova cercando chi ha la porta
9221 aperta e si dichiara PLH a *IDN?. Si sceglie con --host o con la
variabile PSU_HOST.
"""

import argparse
import concurrent.futures
import csv
import os
import socket
import sys
import time

DEFAULT_HOST = os.environ.get("PSU_HOST", "192.168.99.106")
PORT = 9221

# Tetto di tensione per i SiPM collegati. Vale per questo script e per il
# controllore, che lo importa da qui: un errore di battitura come "540" invece
# di "54.0" va fermato prima di arrivare allo strumento, perche' l'OVP dello
# strumento sta a 262.5 V e da solo non protegge niente.
VMAX = 57.0


class PLH:
    def __init__(self, host, timeout=3.0, tentativi=10):
        self.host = host
        # Lo strumento accetta UNA connessione alla volta e rifiuta la seconda
        # (misurato). Il controllore lo interroga ogni pochi secondi: chi
        # arriva in quel momento trova la porta occupata per qualche decina di
        # ms, e riprovare e' la cosa giusta, non un errore.
        for i in range(tentativi):
            try:
                self.sock = socket.create_connection((host, PORT), timeout)
                break
            except ConnectionRefusedError:
                if i == tentativi - 1:
                    raise
                time.sleep(0.2)
        self.sock.settimeout(timeout)
        self.buf = b""

    def close(self):
        # Ogni comando via rete mette lo strumento in remoto, e in remoto i
        # tasti del pannello frontale non rispondono. Restituirlo al pannello
        # a ogni chiusura lascia l'operatore libero di usare la manopola.
        try:
            self.write("LOCAL")
        except OSError:
            pass
        self.sock.close()

    def write(self, cmd):
        self.sock.sendall((cmd + "\n").encode())

    def query(self, cmd):
        self.write(cmd)
        while b"\n" not in self.buf:
            chunk = self.sock.recv(256)
            if not chunk:
                raise ConnectionError("connessione chiusa dallo strumento")
            self.buf += chunk
        riga, self.buf = self.buf.split(b"\n", 1)
        return riga.decode().strip()

    @staticmethod
    def _num(risposta):
        # Le risposte hanno forme diverse: "V1 54.0", "54.05V", "0.00002A".
        return float(risposta.split()[-1].rstrip("VA"))

    def idn(self):
        return self.query("*IDN?")

    def vset(self):
        return self._num(self.query("V1?"))

    def iset(self):
        return self._num(self.query("I1?"))

    def vout(self):
        return self._num(self.query("V1O?"))

    def iout(self):
        return self._num(self.query("I1O?"))

    def output(self):
        return self.query("OP1?") == "1"

    def check_errors(self, cmd):
        # *ESR? azzera il registro: ogni comando si controlla subito dopo,
        # altrimenti un errore resta attribuito al comando sbagliato.
        esr = int(self.query("*ESR?"))
        # bit 4 = errore di esecuzione (valore fuori limite), bit 5 = di sintassi
        if esr & 0x30:
            raise RuntimeError("lo strumento ha rifiutato '%s' (ESR=%d)" % (cmd, esr))

    def send(self, cmd):
        self.write(cmd)
        self.check_errors(cmd)


def controlla_tensione(volt):
    if not 0 <= volt <= VMAX:
        raise ValueError("%.3f V fuori dall'intervallo ammesso 0-%.1f V (VMAX in "
                         "tools/psu_control.py)" % (volt, VMAX))


def ramp(psu, target, step, dwell, passo=None, interrompi=None):
    """Porta la tensione impostata a `target` a passi di `step` volt.

    Un salto di decine di volt su un fotorivelatore polarizzato da' un
    transitorio di corrente che puo' far scattare l'OCP o stressare il
    sensore: la rampa lo evita. Con step <= 0 imposta direttamente.
    `passo(v)` si chiama a ogni passo; se `interrompi()` diventa vero la rampa
    si ferma dov'e'. Torna False se e' stata interrotta.
    """
    controlla_tensione(target)
    v = psu.vset()
    if step > 0:
        segno = 1 if target > v else -1
        while abs(target - v) > step:
            if interrompi and interrompi():
                return False
            v += segno * step
            psu.send("V1 %.3f" % v)
            if passo:
                passo(v)
            time.sleep(dwell)
    psu.send("V1 %.3f" % target)
    if passo:
        passo(target)
    return True


def status(psu):
    print(psu.idn())
    print("uscita      : %s" % ("ACCESA" if psu.output() else "spenta"))
    print("V impostata : %10.3f V    letta: %10.3f V" % (psu.vset(), psu.vout()))
    print("I limite    : %10.6f A    letta: %10.6f A" % (psu.iset(), psu.iout()))
    print("OVP / OCP   : %s V / %s A" % (psu.query("OVP1?"), psu.query("OCP1?")))
    # LSR si azzera a ogni lettura e accumula quello che e' successo dalla
    # lettura precedente: il valore e' storico, non istantaneo.
    print("LSR1        : %s  (latch dall'ultima lettura)" % psu.query("LSR1?"))


def monitor(psu, every, out_csv):
    writer = None
    if out_csv:
        f = open(out_csv, "a", newline="")
        writer = csv.writer(f)
        if f.tell() == 0:
            writer.writerow(["unix_time", "v_set", "v_out", "i_out", "output"])
    try:
        while True:
            t = time.time()
            riga = [t, psu.vset(), psu.vout(), psu.iout(), int(psu.output())]
            print("%s  Vset %8.3f  Vout %8.3f V  Iout %.6f A  %s" % (
                time.strftime("%H:%M:%S", time.localtime(t)),
                riga[1], riga[2], riga[3], "ON" if riga[4] else "off"))
            if writer:
                writer.writerow(riga)
                f.flush()
            time.sleep(every)
    except KeyboardInterrupt:
        pass


def find(subnet, stampa=True):
    def prova(ip):
        try:
            s = socket.create_connection((ip, PORT), 0.5)
            s.settimeout(1.0)
            s.sendall(b"*IDN?\n")
            idn = s.recv(256).decode().strip()
            s.close()
            return ip, idn
        except OSError:
            return None
    base = subnet.rsplit(".", 1)[0]
    ips = ["%s.%d" % (base, i) for i in range(1, 255)]
    with concurrent.futures.ThreadPoolExecutor(64) as ex:
        trovati = [r for r in ex.map(prova, ips) if r]
    for ip, idn in trovati:
        if stampa:
            print("%-16s %s" % (ip, idn))
    if not trovati and stampa:
        print("nessuno strumento sulla porta %d in %s.0/24" % (PORT, base))
    return trovati


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=DEFAULT_HOST)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    p = sub.add_parser("set", help="imposta la tensione (V), in rampa")
    p.add_argument("volt", type=float)
    p.add_argument("--step", type=float, default=5.0, help="V per passo; 0 = salto diretto")
    p.add_argument("--dwell", type=float, default=0.5, help="s fra un passo e l'altro")
    p = sub.add_parser("ilim", help="limite di corrente (A)")
    p.add_argument("amp", type=float)
    sub.add_parser("on")
    sub.add_parser("off")
    p = sub.add_parser("ovp", help="soglia di protezione in tensione (V)")
    p.add_argument("volt", type=float)
    p = sub.add_parser("ocp", help="soglia di protezione in corrente (A)")
    p.add_argument("amp", type=float)
    sub.add_parser("tripreset", help="riarma dopo un intervento di OVP/OCP")
    sub.add_parser("local", help="restituisce il controllo al pannello frontale")
    p = sub.add_parser("monitor")
    p.add_argument("--every", type=float, default=1.0)
    p.add_argument("--csv")
    p = sub.add_parser("find")
    p.add_argument("--subnet", default="192.168.99.0")
    p = sub.add_parser("raw", help="manda un comando qualunque")
    p.add_argument("command")
    a = ap.parse_args()

    if a.cmd == "find":
        sys.exit(0 if find(a.subnet) else 1)

    try:
        psu = PLH(a.host)
    except OSError as e:
        sys.exit("%s:%d non risponde (%s). L'indirizzo e' da DHCP: provare "
                 "`psu_control.py find`." % (a.host, PORT, e))
    try:
        if a.cmd == "status":
            status(psu)
        elif a.cmd == "set":
            ramp(psu, a.volt, a.step, a.dwell,
                 passo=lambda v: print("  V1 = %8.3f V   Vout = %8.3f V   Iout = %.6f A"
                                       % (v, psu.vout(), psu.iout())))
            status(psu)
        elif a.cmd == "ilim":
            psu.send("I1 %.6f" % a.amp)
            status(psu)
        elif a.cmd == "on":
            psu.send("OP1 1")
            status(psu)
        elif a.cmd == "off":
            psu.send("OP1 0")
            status(psu)
        elif a.cmd == "ovp":
            psu.send("OVP1 %.3f" % a.volt)
            status(psu)
        elif a.cmd == "ocp":
            psu.send("OCP1 %.6f" % a.amp)
            status(psu)
        elif a.cmd == "tripreset":
            psu.send("TRIPRST")
            status(psu)
        elif a.cmd == "local":
            psu.write("LOCAL")
        elif a.cmd == "monitor":
            monitor(psu, a.every, a.csv)
        elif a.cmd == "raw":
            if a.command.rstrip().endswith("?"):
                print(psu.query(a.command))
            else:
                psu.send(a.command)
    except (RuntimeError, ValueError, OSError) as e:
        sys.exit(str(e))
    finally:
        psu.close()


if __name__ == "__main__":
    main()
