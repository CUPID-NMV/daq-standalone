#ifndef STOP_H
#define STOP_H

// ---------------------------------------------------------------------------
//  Arresto pulito della run
//
//  Senza questo, l'unico modo di fermare una run era ucciderla, e il prezzo si
//  vedeva nei file: HDF5 lasciato col flag SWMR aperto e quindi rileggibile
//  solo in modalita' live, nessuna compressione finale, e la board non
//  resettata -- da cui il "Cannot reset digitizer" che compariva in coda.
//
//  Qui il segnale non fa altro che alzare un flag: i cicli di acquisizione lo
//  guardano a ogni giro ed escono dalla porta normale, quella che chiude il
//  file, lo comprime e resetta il digitizer.
//
//  Il secondo segnale invece termina subito. Serve una via d'uscita per quando
//  il ciclo e' bloccato sul link: e' gia' successo che ReadData restasse a
//  ritentare, e in quel caso un Ctrl-C che non fa niente e' peggio di niente.
// ---------------------------------------------------------------------------
namespace Stop {

// Installa i gestori per SIGINT e SIGTERM. Da chiamare una volta, da main.
void Install();

// Vero dopo il primo segnale. I cicli lunghi la interrogano a ogni iterazione.
bool Requested();

}  // namespace Stop

#endif  // STOP_H
