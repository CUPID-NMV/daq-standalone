#!/usr/bin/env python3
"""Impulsi di test dal V812, per provare la catena senza segnali veri.

Scrive nel registro di test del modulo (Base + 0x4C): il V812 genera un
impulso su tutti i canali abilitati e quindi sull'uscita OR, qualunque cosa
ci sia agli ingressi (manuale rev.7 par. 4.6).

Serve a dividere in due un "non trigghia": se con questi impulsi il V1742
acquisisce, allora OR, cavo, TRG-IN e configurazione del trigger sono a
posto e il problema sta nella discriminazione dei segnali veri. Se non
acquisisce nemmeno cosi', il problema e' a valle del discriminatore.

Il bus deve essere libero: fermare la DAQ prima di lanciarlo, perche' una
seconda connessione allo stesso V4718 mentre il digitizer e' collegato e'
proprio il genere di cosa che fa cadere il link.

    python3 tools/v812_pulse.py --n 200 --hz 20
    python3 tools/v812_pulse.py --n 0            # a raffica, finche' non si ferma
"""

import argparse
import ctypes as C
import sys
import time

ETH_V4718 = 27          # CVBoardTypes: Ethernet verso il V4718, accesso al bus
A32_U_DATA = 0x09
D16 = 0x02

REG_TEST_PULSE = 0x4C
REG_FIXED_CODE = 0xFA
V812_FIXED_CODE = 0xFAF5


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ip", default="192.168.99.105", help="indirizzo del bridge V4718")
    ap.add_argument("--base", default="0xDDDD0000", help="indirizzo di base del V812")
    ap.add_argument("--n", type=int, default=200, help="quanti impulsi (0 = senza fine)")
    ap.add_argument("--hz", type=float, default=20.0, help="frequenza degli impulsi")
    args = ap.parse_args()

    base = int(args.base, 0)
    lib = C.CDLL("libCAENVME.so")

    h = C.c_int32(-1)
    if lib.CAENVME_Init2(C.c_int(ETH_V4718), args.ip.encode(), C.c_short(0), C.byref(h)) != 0:
        sys.exit("Non riesco ad aprire il bridge su %s.\n"
                 "Se la DAQ sta girando, fermala: il bus non si condivide." % args.ip)

    # Verifica di stare parlando davvero col V812 prima di scriverci dentro.
    d = C.c_uint32(0)
    if lib.CAENVME_ReadCycle(h, C.c_uint32(base + REG_FIXED_CODE), C.byref(d),
                             C.c_int(A32_U_DATA), C.c_int(D16)) != 0:
        lib.CAENVME_End(h)
        sys.exit("Nessuna risposta a 0x%08X: controlla l'indirizzo." % base)
    if (d.value & 0xFFFF) != V812_FIXED_CODE:
        lib.CAENVME_End(h)
        sys.exit("A 0x%08X il codice fisso vale 0x%04X invece di 0x%04X: non e' un V812."
                 % (base, d.value & 0xFFFF, V812_FIXED_CODE))

    print("V812 riconosciuto a 0x%08X. Impulsi di test su 0x%08X."
          % (base, base + REG_TEST_PULSE))
    print("Guarda il LED OR sul pannello e, se puoi, l'uscita OR all'oscilloscopio.")

    # Il valore scritto e' indifferente: conta l'accesso in scrittura.
    dato = C.c_uint32(1)
    passo = 1.0 / args.hz if args.hz > 0 else 0.0
    n = 0
    try:
        while args.n == 0 or n < args.n:
            if lib.CAENVME_WriteCycle(h, C.c_uint32(base + REG_TEST_PULSE), C.byref(dato),
                                      C.c_int(A32_U_DATA), C.c_int(D16)) != 0:
                print("scrittura fallita dopo %d impulsi" % n)
                break
            n += 1
            if n % 50 == 0:
                print("  %d impulsi" % n, flush=True)
            if passo:
                time.sleep(passo)
    except KeyboardInterrupt:
        pass
    finally:
        lib.CAENVME_End(h)
    print("inviati %d impulsi di test" % n)


if __name__ == "__main__":
    main()
