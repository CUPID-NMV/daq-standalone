#include <iostream>

#include "Config.h"
#include "Log.h"
#include "Bridge.h"
#include "Digitizer.h"

int main(int argc, char** argv)
{
    if (argc != 2)
    {
        std::cout << "You need to supply the toml config-file (and only that) to the program." << std::endl;
        std::cout << "Usage: ./GAGG-DAQ /path/to/config-file.toml" << std::endl;
        return 1;
    }

    // --- Config ---
    Config& theConfig = Config::GetInstance();
    theConfig.Read(argv[1]);

    Log::OpenLog(Log::LogLevel::debug);
    Log::OutSummary("* * * * * * * * * * * * * * *");
    Log::OutSummary("*                           *");
    Log::OutSummary("*  Welcome to the GAGG DAQ  *");
    Log::OutSummary("*                           *");
    Log::OutSummary("* * * * * * * * * * * * * * *");
    Log::OutSummary();

    // --- Bridge VME (V4718) ---
    Bridge bridge(theConfig);
    bridge.Open();

    // --- Digitizer V1742 ---
    Digitizer digitizer;
    digitizer.SelectBoard();    // 1. Connessione al V1742
    digitizer.Configure();      // 2. Configurazione base (record length, gruppi, canali, offset, ecc.)
    digitizer.InitAcquisition();// 3. Allocazione buffer/evento

    // 4. Calcolo baseline UNA sola volta
    digitizer.SetTriggerThreshold(0.1);

    // 5. Configurazione sorgenti di trigger (TRG-IN esterno e/o self-trigger
    //    dai canali di input), secondo quanto richiesto dal file di config.
    digitizer.ConfigureTrigger();

    // 6. Output HDF5 + acquisizione
    digitizer.PrepareOutput();   // crea file HDF5, gruppo "/events" e "/config"
    digitizer.AcquireEvents();   // legge eventi e li scrive in HDF5 (chiama anche CloseOutputFile() alla fine)

    // 7. Reset del digitizer (su handle ancora aperto)
    digitizer.Reset();

    // 8. Chiusura risorse CAEN (Close() libera buffer/eventi e chiude il digitizer)
    digitizer.Close();

    // 9. Bridge off
    bridge.Close();

    return 0;
}