#include <iostream>

#include "Config.h"
#include "Log.h"
#include "Stop.h"
#include "Bridge.h"
#include "CFD.h"
#include "Digitizer.h"

int main(int argc, char** argv)
{
    if (argc != 2)
    {
        std::cout << "You need to supply the toml config-file (and only that) to the program." << std::endl;
        std::cout << "Usage: ./GAGG-DAQ /path/to/config-file.toml" << std::endl;
        return 1;
    }

    // Installato subito: da qui in poi un Ctrl-C, o il SIGTERM che manda chi
    // ferma la run da fuori, chiude la run per la porta buona invece di
    // ucciderla. Un secondo segnale esce comunque, per quando il link e'
    // appeso e il ciclo non risponde.
    Stop::Install();

    // --- Config ---
    Config& theConfig = Config::GetInstance();
    theConfig.Read(argv[1]);

    // Il livello lo decide [settings][verbosity] nel TOML, letto da
    // Config::Read: forzarlo qui rendeva quell'opzione inerte.
    Log::OutSummary("* * * * * * * * * * * * * * *");
    Log::OutSummary("*                           *");
    Log::OutSummary("*  Welcome to the GAGG DAQ  *");
    Log::OutSummary("*                           *");
    Log::OutSummary("* * * * * * * * * * * * * * *");
    Log::OutSummary();

    // --- Digitizer V1742 ---
    // La connessione va aperta PRIMA del bridge: con "Connection = auto" e'
    // il digitizer a stabilire quale collegamento si usa, e sul percorso
    // CONET diretto (A4818) il bridge VME non esiste proprio.
    Digitizer digitizer;
    digitizer.SelectBoard();    // 1. Connessione al V1742

    // --- Bridge VME, solo se serve ---
    Bridge bridge(theConfig);
    const bool useBridge = digitizer.UsesVMEBridge();
    if (useBridge)
        bridge.Open();

    // --- Discriminatore CFD V812, facoltativo ---
    // Configurato subito dopo il bridge, prima della lunga messa a punto del
    // digitizer: se l'indirizzo e' sbagliato conviene scoprirlo adesso.
    // Senza la sezione [cfd] nel TOML non succede niente.
    CFD cfd(theConfig, bridge);
    cfd.Configure();

    digitizer.Configure();      // 2. Configurazione base (record length, gruppi, canali, offset, ecc.)
    digitizer.InitAcquisition();// 3. Allocazione buffer/evento

    // 4. Calcolo baseline UNA sola volta
    digitizer.SetTriggerThreshold(0.1);

    // 5. Configurazione sorgenti di trigger (TRG-IN esterno e/o self-trigger
    //    dai canali di input), secondo quanto richiesto dal file di config.
    digitizer.ConfigureTrigger();

    // 6. Output HDF5 + acquisizione
    // Le soglie del CFD finiscono nell'header solo se il modulo e' attivo:
    // i file delle run senza CFD restano identici a prima.
    if (cfd.Enabled())
        digitizer.SetCFDInfo(cfd.BaseAddress(), cfd.Channels(), cfd.ThresholdsMv());

    digitizer.PrepareOutput();   // crea file HDF5, gruppo "/events" e "/config"

    if (digitizer.TransparentDumpRequested())
        // Diagnostica: registra cio' che vede il discriminatore, poi termina.
        digitizer.AcquireTransparent();
    else
        digitizer.AcquireEvents();   // legge eventi e li scrive in HDF5

    // 7. Reset del digitizer (su handle ancora aperto)
    digitizer.Reset();

    // 8. Chiusura risorse CAEN (Close() libera buffer/eventi e chiude il digitizer)
    digitizer.Close();

    Log::CloseFile();

    // 9. Bridge off
    if (useBridge)
        bridge.Close();

    return 0;
}