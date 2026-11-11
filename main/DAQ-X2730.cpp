#include <iostream>
#include <string>

#include "Config.h"
#include "Log.h"
#include "Stop.h"
#include "X2730.h"

int main(int argc, char** argv)
{
    // DAQ-X2730 config.toml [--run N]
    //
    // Legge un VX2730B (digitizer 2.0, firmware Scope) con la CAEN FELib.
    // La configurazione e' nella sezione [vx2730] dello stesso TOML del V1742.
    // --run impone il numero di run: con piu' schede lo da' il controllore,
    // lo stesso a tutte, come per DAQ-WC --board.
    int runNumber = -1;
    const char* configFile = nullptr;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--run" && i + 1 < argc) runNumber = std::stoi(argv[++i]);
        else if (configFile == nullptr && a.rfind("--", 0) != 0) configFile = argv[i];
        else { configFile = nullptr; break; }
    }
    if (configFile == nullptr) {
        std::cout << "Usage: ./DAQ-X2730 /path/to/config-file.toml [--run N]" << std::endl;
        return 1;
    }

    // Subito, come in DAQ-WC: Ctrl-C o SIGTERM chiudono la run per la porta
    // buona (file HDF5 chiuso regolarmente), un secondo segnale esce comunque.
    Stop::Install();

    Config& theConfig = Config::GetInstance();
    theConfig.Read(const_cast<char*>(configFile));
    theConfig.SetRunNumber(runNumber);

    Log::OutSummary("DAQ-X2730: VX2730B via CAEN FELib");

    X2730 board(theConfig);
    board.Open();
    board.Configure();
    board.PrepareOutput();
    board.Acquire();
    board.Close();

    Log::CloseFile();
    return 0;
}
