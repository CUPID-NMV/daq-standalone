#include <iostream>
#include <string>

#include "Config.h"
#include "Log.h"
#include "Stop.h"
#include "Bridge.h"
#include "CFD.h"
#include "Digitizer.h"
#ifdef DAQ_HAVE_FELIB
#include "X2730.h"
#endif

#include <algorithm>
#include <cctype>

namespace {

// I digitizer 2.0 di CAEN (VX2730B, e le famiglie 2740, 2745, 2751 che
// condividono FELib e parametri): li legge la classe X2730 con la FELib. Si
// riconoscono dal Model scritto in [boards.<nome>]; senza Model la scheda e'
// un V1742, come sempre.
bool Digitizer2(const std::string& model)
{
    std::string m = model;
    std::transform(m.begin(), m.end(), m.begin(), [](unsigned char c) { return std::tolower(c); });
    for (const char* fam : {"2730", "2740", "2745", "2751"})
        if (m.find(fam) != std::string::npos) return true;
    return false;
}

int AcquisisciDigitizer2(Config& cfg, const std::string& board)
{
#ifdef DAQ_HAVE_FELIB
    cfg.SelectBoardOnly(board);
    X2730 scheda(cfg, board);
    scheda.Open();
    scheda.Configure();
    scheda.PrepareOutput();
    scheda.Acquire();
    scheda.Close();
    Log::CloseFile();
    return 0;
#else
    // Il PC del Gran Sasso non ha la FELib: la DAQ si compila senza, e una
    // scheda 2.0 qui si rifiuta per nome invece di cadere chissa' dove.
    Log::OutError("La scheda '" + board + "' e' un " + cfg.BoardModel(board) +
                  " (digitizer 2.0): questo DAQ-WC e' compilato senza la CAEN FELib.");
    return 1;
#endif
}

}  // namespace

int main(int argc, char** argv)
{
    // DAQ-WC config.toml [--board NOME] [--run N]
    //
    // --board  sceglie la sezione [boards.NOME] del TOML: con piu' digitizer
    //          gira un processo per scheda. Senza, tutto come prima.
    // --run    numero di run imposto: con piu' schede lo da' il controllore,
    //          lo stesso a tutte, perche' contando i file ognuna lo
    //          calcolerebbe a seconda di chi ha gia' creato il suo.
    std::string board;
    int runNumber = -1;
    const char* configFile = nullptr;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--board" && i + 1 < argc)      board = argv[++i];
        else if (a == "--run" && i + 1 < argc)   runNumber = std::stoi(argv[++i]);
        else if (configFile == nullptr && a.rfind("--", 0) != 0) configFile = argv[i];
        else { configFile = nullptr; break; }
    }
    if (configFile == nullptr)
    {
        std::cout << "Usage: ./DAQ-WC /path/to/config-file.toml [--board NAME] [--run N]" << std::endl;
        return 1;
    }

    // Installato subito: da qui in poi un Ctrl-C, o il SIGTERM che manda chi
    // ferma la run da fuori, chiude la run per la porta buona invece di
    // ucciderla. Un secondo segnale esce comunque, per quando il link e'
    // appeso e il ciclo non risponde.
    Stop::Install();

    // --- Config ---
    Config& theConfig = Config::GetInstance();
    theConfig.Read(const_cast<char*>(configFile));
    const std::string model = board.empty() ? std::string() : theConfig.BoardModel(board);
    const bool digitizer2 = Digitizer2(model);
    if (!model.empty() && !digitizer2 && model.find("1742") == std::string::npos) {
        Log::OutError("[boards." + board + "] Model = '" + model + "': la DAQ conosce il V1742 "
                      "(Model assente o \"V1742\") e i digitizer 2.0 (\"VX2730B\"...).");
        return 1;
    }
    if (!board.empty() && !digitizer2)
        theConfig.SelectBoard(board);
    theConfig.SetRunNumber(runNumber);

    // Il livello lo decide [settings][verbosity] nel TOML, letto da
    // Config::Read: forzarlo qui rendeva quell'opzione inerte.
    Log::OutSummary("* * * * * * * * * * * * * * *");
    Log::OutSummary("*                           *");
    Log::OutSummary("*  Welcome to the GAGG DAQ  *");
    Log::OutSummary("*                           *");
    Log::OutSummary("* * * * * * * * * * * * * * *");
    Log::OutSummary();

    // --- Digitizer 2.0 (VX2730B): codice suo, file e log nello stesso formato ---
    if (digitizer2)
        return AcquisisciDigitizer2(theConfig, board);

    // --- Digitizer V1742 ---
    // La connessione va aperta PRIMA del bridge: con "Connection = auto" e'
    // il digitizer a stabilire quale collegamento si usa, e sul percorso
    // CONET diretto (A4818) il bridge VME non esiste proprio.
    Digitizer digitizer;
    digitizer.SelectBoard();    // 1. Connessione al V1742

    // --- Bridge VME, solo se serve ---
    // Il V812 e' uno solo e lo configura UNA sola scheda, quella indicata da
    // [cfd] Board: due processi che lo scrivono insieme farebbero solo
    // confusione. Il bridge serve soltanto al V812, quindi chi non lo
    // configura non lo apre nemmeno.
    bool doCFD = true;
    if (!board.empty()) {
        std::string cfdBoard = theConfig.GetTbl()["cfd"]["Board"].value_or(std::string());
        doCFD = (cfdBoard == board);
        if (cfdBoard.empty() && theConfig.GetTbl()["cfd"]["Enabled"].value_or(false))
            Log::OutWarning("[cfd] Board non indicato: con piu' schede il V812 lo configura "
                            "solo quella scritta li', quindi nessuna lo configura.");
    }
    Bridge bridge(theConfig);
    const bool useBridge = doCFD && digitizer.UsesVMEBridge();
    if (useBridge)
        bridge.Open();

    // --- Discriminatore CFD V812, facoltativo ---
    // Configurato subito dopo il bridge, prima della lunga messa a punto del
    // digitizer: se l'indirizzo e' sbagliato conviene scoprirlo adesso.
    // Senza la sezione [cfd] nel TOML non succede niente.
    CFD cfd(theConfig, bridge);
    if (doCFD)
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