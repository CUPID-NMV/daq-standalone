#!/usr/bin/env python3
"""
Elenchi di canali scritti a mano in una casella di una pagina.

Modulo senza dipendenze apposta: lo usano il monitor, la pagina di controllo
e daqio, e due copie dello stesso parser divergono -- e' gia' successo. Il
monitor capiva "12-15" e il controllore no, cosi' un intervallo scritto nella
casella dei canali veniva buttato via in silenzio e il grafico usciva con
TUTTI i canali invece dei tre chiesti.
"""


def parse_channels(testo):
    """Interpreta "8,9,12-15" come [8, 9, 12, 13, 14, 15]. Vuoto = None.

    Sta qui e non in una delle due pagine perche' lo usano tutt'e due, e due
    copie divergono: il monitor capiva gli intervalli e il controllore no, e
    un "16-18" scritto nella casella dei canali veniva buttato via in
    silenzio, disegnando TUTTI i canali invece dei tre chiesti.

    Un intervallo scritto al contrario o un pezzo non numerico vengono
    ignorati: e' una casella di testo in una pagina, e non deve poter far
    cadere il server. Chi chiama distingue "vuoto" da "non ho capito niente"
    confrontando con il testo di partenza.
    """
    testo = (testo or "").strip()
    if not testo:
        return None
    fuori = []
    for pezzo in testo.replace(";", ",").replace(" ", ",").split(","):
        pezzo = pezzo.strip()
        if not pezzo:
            continue
        if "-" in pezzo:
            a, _, b = pezzo.partition("-")
            try:
                a, b = int(a), int(b)
            except ValueError:
                continue
            if a <= b:
                fuori.extend(range(a, b + 1))
        else:
            try:
                fuori.append(int(pezzo))
            except ValueError:
                continue
    # senza duplicati e in ordine, cosi' i grafici non dipendono da come si
    # e' scritto l'elenco
    return sorted(set(fuori)) or None
