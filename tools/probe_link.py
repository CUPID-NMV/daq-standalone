#!/usr/bin/env python3
"""Trova come aprire il digitizer su un dato collegamento.

L'header della libreria, per il parametro `arg` di CAEN_DGTZ_OpenDigitizer2,
dice soltanto "See documentation": la convenzione cambia da un tipo di link
all'altro e non e' scritta da nessuna parte nei sorgenti. Questo strumento la
determina provando le possibilita' plausibili e riportando quale apre davvero,
invece di lasciare l'incertezza dentro la DAQ.

    python3 tools/probe_link.py --pid 49826
    python3 tools/probe_link.py --ip 192.168.99.105

Va lanciato con nessuna acquisizione in corso: apre e chiude il digitizer.
"""

import argparse
import ctypes
import os

MAX_LICENSE_LENGTH = 8 * 2 + 1   # come in CAENDigitizerType.h

LINK = {
    "USB":                0,
    "OpticalLink":        1,
    "USB_A4818_V2718":    2,
    "USB_A4818_V3718":    3,
    "USB_A4818_V4718":    4,
    "USB_A4818":          5,
    "ETH_V4718":          6,
    "USB_V4718":          7,
}


class BoardInfo(ctypes.Structure):
    _fields_ = [
        ("ModelName", ctypes.c_char * 12),
        ("Model", ctypes.c_uint32),
        ("Channels", ctypes.c_uint32),
        ("FormFactor", ctypes.c_uint32),
        ("FamilyCode", ctypes.c_uint32),
        ("ROC_FirmwareRel", ctypes.c_char * 20),
        ("AMC_FirmwareRel", ctypes.c_char * 40),
        ("SerialNumber", ctypes.c_uint32),
        ("MezzanineSerNum", (ctypes.c_char * 8) * 4),
        ("PCB_Revision", ctypes.c_uint32),
        ("ADC_NBits", ctypes.c_uint32),
        ("SAMCorrectionDataLoaded", ctypes.c_uint32),
        ("CommHandle", ctypes.c_int),
        ("VMEHandle", ctypes.c_int),
        ("License", ctypes.c_char * MAX_LICENSE_LENGTH),
    ]


def prova(lib, nome_link, arg, descr, conet, vme):
    h = ctypes.c_int(-1)
    ret = lib.CAEN_DGTZ_OpenDigitizer2(ctypes.c_int(LINK[nome_link]), arg,
                                       ctypes.c_int(conet), ctypes.c_uint32(vme),
                                       ctypes.byref(h))
    esito = "  link=%-16s arg=%-22s conet=%d vme=0x%08X  -> " % (
        nome_link, descr, conet, vme)
    if ret != 0:
        print(esito + "codice %d" % ret)
        return False
    info = BoardInfo()
    gi = lib.CAEN_DGTZ_GetInfo(h, ctypes.byref(info))
    modello = info.ModelName.decode(errors="replace") if gi == 0 else "?"
    print(esito + "APERTO  handle=%d  modello=%s  seriale=%d  ROC=%s AMC=%s" % (
        h.value, modello, info.SerialNumber,
        info.ROC_FirmwareRel.decode(errors="replace"),
        info.AMC_FirmwareRel.decode(errors="replace")))
    lib.CAEN_DGTZ_CloseDigitizer(h)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, help="PID dell'A4818, stampato sul modulo")
    ap.add_argument("--ip", help="indirizzo del V4718 via Ethernet")
    ap.add_argument("--conet", type=int, default=0)
    ap.add_argument("--lib", default="libCAENDigitizer.so")
    args = ap.parse_args()
    if not args.pid and not args.ip:
        ap.error("serve almeno --pid o --ip")

    lib = ctypes.CDLL(args.lib)
    trovati = []

    if args.pid is not None:
        print("A4818, PID %d:" % args.pid)
        s = str(args.pid).encode()
        n = ctypes.c_uint32(args.pid)
        # La base VME e' 0 quando la fibra va direttamente alla board: il
        # digitizer e' il nodo CONET, non uno slave su un bus VME.
        for vme in (0x0, 0x32100000):
            for arg, descr in ((ctypes.c_char_p(s), '"%d" (stringa)' % args.pid),
                               (ctypes.byref(n), "%d (uint32)" % args.pid)):
                for link in ("USB_A4818", "USB_A4818_V4718"):
                    if prova(lib, link, arg, descr, args.conet, vme):
                        trovati.append((link, descr, args.conet, vme))
        print()

    if args.ip:
        print("V4718 via Ethernet, %s:" % args.ip)
        a = ctypes.c_char_p(args.ip.encode())
        for vme in (0x32100000, 0x0):
            if prova(lib, "ETH_V4718", a, '"%s"' % args.ip, args.conet, vme):
                trovati.append(("ETH_V4718", args.ip, args.conet, vme))
        print()

    if trovati:
        print("combinazioni funzionanti:")
        for t in trovati:
            print("   link=%s  arg=%s  conet=%d  vme=0x%08X" % t)
    else:
        print("nessuna combinazione ha aperto il digitizer.")


if __name__ == "__main__":
    main()
