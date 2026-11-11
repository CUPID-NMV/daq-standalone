#include "X2730.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <ctime>
#include <filesystem>
#include <iomanip>
#include <random>
#include <sstream>
#include <thread>

extern "C" {
#include <CAEN_FELib.h>
}

#include "Log.h"
#include "Stop.h"

namespace {

constexpr const char* kSection = "vx2730";
constexpr int kFormatVersion = 3;          // lo stesso del V1742: daqio lo legge uguale
constexpr hsize_t kChunkEvents = 8;
constexpr long kFlushMaxDelayMs = 300;     // come il V1742: il monitor vede i dati entro 0.3 s

std::string Num(double x, int prec = 6) {
    std::ostringstream s;
    s << std::setprecision(prec) << x;
    return s.str();
}

std::string Lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return std::tolower(c); });
    return s;
}

std::string Join(const std::vector<std::string>& v, const std::string& sep) {
    std::string out;
    for (size_t i = 0; i < v.size(); ++i) out += (i ? sep : "") + v[i];
    return out;
}

}  // namespace

// ===========================================================================
//  Configurazione
// ===========================================================================

template <typename T>
std::vector<T> X2730::ScalarOrList(const std::string& key, T def, size_t n) {
    // Uno scalare vale per tutti i canali, una lista e' parallela a
    // ChannelList: lo stesso uso del DCOffset per canale del V1742.
    auto node = fConfig.GetTbl()[kSection][key];
    if (node.is_array())
        return fConfig.GetEntryList<T>(kSection, key, def, n);
    T v = node.value_or(def);
    if (!node) fConfig.WarnMissing(kSection, key);
    return std::vector<T>(n, v);
}

X2730::X2730(Config& cfg) : fConfig(cfg) {
    auto tbl = fConfig.GetTbl()[kSection];
    if (!tbl.as_table()) {
        Log::OutError("Manca la sezione [vx2730] nel TOML: e' li' che sta la configurazione "
                      "del VX2730B (vedi config/template-daq.toml).");
        exit(1);
    }
    fUrl        = fConfig.GetEntry<std::string>(kSection, "URL", "dig2://caen.internal/usb/71709");
    fBoard      = fConfig.GetEntry<std::string>(kSection, "Board", "vx2730");
    // OutputDir e OutputFile, se non dati qui, sono quelli del V1742: con piu'
    // schede la stessa cartella e lo stesso prefisso tengono i file di una run
    // vicini, distinti dal nome della scheda dopo il numero.
    std::string od = fConfig.GetTbl()["digitizer"]["OutputDir"].value_or(std::string("."));
    std::string of = fConfig.GetTbl()["digitizer"]["OutputFile"].value_or(std::string("test"));
    fOutputDir  = tbl["OutputDir"].value_or(od);
    fOutputFile = tbl["OutputFile"].value_or(of);
    fSaveData   = tbl["SaveData"].value_or(true);
    fNEvents    = fConfig.GetEntry<uint32_t>(kSection, "NEvents", 1000);
    fMaxTime    = tbl["MaxTime"].value_or(0.0);

    if (auto arr = tbl["ChannelList"].as_array()) {
        for (size_t i = 0; i < arr->size(); ++i)
            fChannels.push_back(static_cast<uint32_t>((*arr)[i].value_or(-1)));
    } else {
        fConfig.WarnMissing(kSection, "ChannelList");
        for (uint32_t c = 0; c < 16; ++c) fChannels.push_back(c);
    }
    if (fChannels.empty()) {
        Log::OutError("[vx2730] ChannelList e' vuota: nessun canale da acquisire.");
        exit(1);
    }

    fRecordLength = fConfig.GetEntry<uint32_t>(kSection, "RecordLength", 1024);
    fPreTrigger   = fConfig.GetEntry<uint32_t>(kSection, "PreTrigger", 256);
    fDCOffset     = ScalarOrList<double>("DCOffset", 50.0, fChannels.size());
    fGain         = ScalarOrList<double>("Gain", 0.0, fChannels.size());
    fIOLevel      = tbl["IOLevel"].value_or(std::string("NIM"));
    fFlushEvery   = std::max<uint32_t>(1, tbl["LiveFlushEvery"].value_or(1u));

    // Il verso dell'impulso decide insieme fronte e segno della soglia del
    // self-trigger. Sono una cosa sola anche qui: sbagliarne uno non da'
    // errori, la soglia si posa dove il segnale non va mai e la run resta a
    // zero eventi (successo col V1742 passando dai PMT ai SiPM).
    fPolarity = Lower(fConfig.GetEntry<std::string>(kSection, "Polarity", "positive"));
    if (fPolarity != "positive" && fPolarity != "negative") {
        Log::OutError("[vx2730] Polarity = '" + fPolarity + "': vale \"positive\" o \"negative\".");
        exit(1);
    }

    // Sorgenti del trigger, coi nomi del V1742 dove ci sono: Software,
    // External, Self. Il resto passa com'e' alla FELib (TestPulse, LVDS...).
    std::string src = fConfig.GetEntry<std::string>(kSection, "TriggerSource", "Software");
    std::vector<std::string> felib, modo;
    std::string tok;
    std::stringstream ss(src);
    while (std::getline(ss, tok, '|')) {
        tok.erase(std::remove_if(tok.begin(), tok.end(), ::isspace), tok.end());
        if (tok.empty()) continue;
        const std::string t = Lower(tok);
        if (t == "software" || t == "swtrg")      { felib.push_back("SwTrg"); modo.push_back("Software"); fSoftwareTrigger = true; }
        else if (t == "external" || t == "trgin") { felib.push_back("TrgIn"); modo.push_back("External"); }
        else if (t == "self" || t == "itla")      { felib.push_back("ITLA");  modo.push_back("Self"); fSelfTrigger = true; }
        else                                      { felib.push_back(tok);     modo.push_back(tok); }
    }
    if (felib.empty()) {
        Log::OutError("[vx2730] TriggerSource vuoto.");
        exit(1);
    }
    fTriggerSourceFelib = Join(felib, "|");
    fTriggerMode = Join(modo, "+");
    fSWTriggerRate = tbl["SWTriggerRate"].value_or(100.0);

    if (fSelfTrigger) {
        if (auto arr = tbl["SelfTriggerChannels"].as_array()) {
            for (size_t i = 0; i < arr->size(); ++i)
                fSelfChannels.push_back(static_cast<uint32_t>((*arr)[i].value_or(-1)));
        } else {
            fSelfChannels = fChannels;
        }
        fSelfThreshold = ScalarOrList<int32_t>("SelfTriggerThreshold", 100, fSelfChannels.size());
        for (auto& t : fSelfThreshold) t = std::abs(t);
        fSelfLogic = tbl["SelfTriggerLogic"].value_or(std::string("OR"));
        fSelfMajority = tbl["SelfTriggerMajority"].value_or(2u);
    }
}

X2730::~X2730() {
    if (fFile) CloseOutputFile();
    if (fOpen) CAEN_FELib_Close(fDev);
}

// ===========================================================================
//  FELib
// ===========================================================================

std::string X2730::LastError() const {
    char d[1024] = {0};
    CAEN_FELib_GetLastError(d);
    return d;
}

void X2730::Fail(const std::string& what) const {
    Log::OutError(what + ": " + LastError());
    exit(1);
}

void X2730::Set(const std::string& path, const std::string& value) {
    if (CAEN_FELib_SetValue(fDev, path.c_str(), value.c_str()) != CAEN_FELib_Success)
        Fail("SetValue " + path + " = " + value);
    Log::OutDebug("   " + path + " = " + value);
}

std::string X2730::Get(const std::string& path, bool fatal) {
    char v[256] = {0};     // la FELib ne scrive fino a 256: un buffer piu' corto va fuori
    if (CAEN_FELib_GetValue(fDev, path.c_str(), v) != CAEN_FELib_Success) {
        if (fatal) Fail("GetValue " + path);
        return "";
    }
    return v;
}

// ===========================================================================
//  Collegamento
// ===========================================================================

void X2730::Open() {
    Log::OutSummary("→ Opening " + fUrl);
    if (CAEN_FELib_Open(fUrl.c_str(), &fDev) != CAEN_FELib_Success)
        Fail("Cannot open " + fUrl + " (via USB: dig2://caen.internal/usb/<serial number>)");
    fOpen = true;

    char ver[16] = {0};
    CAEN_FELib_GetImplLibVersion(fDev, ver);
    fModel   = Get("/par/ModelName");
    fFwType  = Get("/par/FwType");
    fFpgaFw  = Get("/par/FPGA_FwVer");
    fSerial  = static_cast<uint32_t>(std::stoul(Get("/par/SerialNum")));
    fNumCh   = static_cast<uint32_t>(std::stoul(Get("/par/NumCh")));
    fAdcBits = static_cast<uint32_t>(std::stoul(Get("/par/ADC_Nbit")));
    const double msps = std::stod(Get("/par/ADC_SamplRate"));
    fSamplingNs = 1000.0 / msps;

    Log::OutSummary("→ " + fModel + " s/n " + std::to_string(fSerial) + ", firmware " + fFwType +
                    " " + fFpgaFw + ", " + std::to_string(fNumCh) + " channels, " +
                    std::to_string(fAdcBits) + " bit, " + Num(msps) + " MS/s (Dig2 " + ver + ")");

    // Il programma sa leggere solo il firmware Scope: con un DPP gli eventi
    // sono per canale e il formato dei dati e' un altro.
    if (fFwType != "Scope") {
        Log::OutError("Firmware " + fFwType + ": DAQ-X2730 legge solo il firmware Scope.");
        exit(1);
    }
    for (auto c : fChannels)
        if (c >= fNumCh) {
            Log::OutError("[vx2730] ChannelList: il canale " + std::to_string(c) +
                          " non esiste (la scheda ne ha " + std::to_string(fNumCh) + ").");
            exit(1);
        }
    LogBoardStatus("at start");
}

void X2730::LogBoardStatus(const std::string& when) {
    // Temperature ed errori della scheda, nel log della run: il LED FAIL
    // della scheda vuol dire tensioni, temperatura o PLL, e ErrorFlags dice
    // quale (scheda tecnica DS9688).
    const std::string ef = Get("/par/ErrorFlags", false);
    Log::OutSummary("→ Board " + when + ": T air in/out " + Get("/par/TempSensAirIn", false) + "/" +
                    Get("/par/TempSensAirOut", false) + " C, FPGA " + Get("/par/TempSensCore", false) +
                    " C, hottest ADC " + Get("/par/TempSensHottestADC", false) + " C, clock " +
                    Get("/par/ClockSource", false) + ", ErrorFlags " + ef);
    if (!ef.empty() && ef != "0")
        Log::OutWarning("→ ErrorFlags = " + ef + ": the board reports an error (see the web "
                        "interface, board status).");
}

// ===========================================================================
//  Configurazione della scheda
// ===========================================================================

void X2730::Configure() {
    // Si parte sempre dalla configurazione di fabbrica: quello che non e'
    // scritto nel TOML non deve dipendere da chi ha usato la scheda prima.
    if (CAEN_FELib_SendCommand(fDev, "/cmd/Reset") != CAEN_FELib_Success)
        Fail("Reset");

    // Prima si spengono tutti, poi si accendono quelli della lista: un canale
    // acceso e non scritto nel file occuperebbe banda per niente.
    Set("/ch/0.." + std::to_string(fNumCh - 1) + "/par/ChEnable", "False");
    for (size_t k = 0; k < fChannels.size(); ++k) {
        const std::string ch = "/ch/" + std::to_string(fChannels[k]) + "/par/";
        Set(ch + "ChEnable", "True");
        Set(ch + "DCOffset", Num(fDCOffset[k]));
        Set(ch + "ChGain", Num(fGain[k]));
    }

    Set("/par/RecordLengthS", std::to_string(fRecordLength));
    Set("/par/PreTriggerS", std::to_string(fPreTrigger));
    // La scheda arrotonda (passi di 4 campioni): conta quello che ha preso.
    const uint32_t rl = static_cast<uint32_t>(std::stoul(Get("/par/RecordLengthS")));
    const uint32_t pt = static_cast<uint32_t>(std::stoul(Get("/par/PreTriggerS")));
    if (rl != fRecordLength || pt != fPreTrigger)
        Log::OutWarning("→ Record length / pre-trigger rounded by the board: " +
                        std::to_string(fRecordLength) + "/" + std::to_string(fPreTrigger) +
                        " -> " + std::to_string(rl) + "/" + std::to_string(pt) + " samples");
    fRecordLength = rl;
    fPreTrigger = pt;

    Set("/par/IOLevel", fIOLevel);
    Set("/par/AcqTriggerSource", fTriggerSourceFelib);
    Set("/par/StartSource", "SWcmd");

    if (fSelfTrigger) {
        const std::string edge = (fPolarity == "positive") ? "RISE" : "FALL";
        const int segno = (fPolarity == "positive") ? 1 : -1;
        uint64_t mask = 0;
        for (size_t k = 0; k < fSelfChannels.size(); ++k) {
            const uint32_t c = fSelfChannels[k];
            if (c >= fNumCh) {
                Log::OutError("[vx2730] SelfTriggerChannels: il canale " + std::to_string(c) +
                              " non esiste.");
                exit(1);
            }
            const std::string ch = "/ch/" + std::to_string(c) + "/par/";
            Set(ch + "TriggerThrMode", "Relative");
            Set(ch + "TriggerThr", std::to_string(segno * fSelfThreshold[k]));
            Set(ch + "SelfTriggerEdge", edge);
            Set(ch + "ITLConnect", "ITLA");
            mask |= (uint64_t{1} << c);
        }
        Set("/par/ITLAMask", std::to_string(mask));
        Set("/par/ITLAMainLogic", fSelfLogic);
        if (fSelfLogic == "Majority")
            Set("/par/ITLAMajorityLev", std::to_string(fSelfMajority));
        std::string thr;
        for (size_t k = 0; k < fSelfChannels.size(); ++k)
            thr += " ch" + std::to_string(fSelfChannels[k]) + "=" +
                   std::to_string(segno * fSelfThreshold[k]);
        Log::OutSummary("→ Self-trigger (ITLA, " + fSelfLogic +
                        (fSelfLogic == "Majority" ? " " + std::to_string(fSelfMajority) : "") +
                        ", edge " + edge + "), threshold relative to the baseline [ADC counts]:" + thr);
    }

    fAdcToVolts.clear();
    for (auto c : fChannels)
        fAdcToVolts.push_back(std::stod(Get("/ch/" + std::to_string(c) + "/par/ADCToVolts")));

    // Riassunto per canale nel log: e' l'unico posto che dira' fra sei mesi
    // con che offset e guadagno e' stata presa la run.
    for (size_t k = 0; k < fChannels.size(); ++k)
        Log::OutSummary("   ch" + std::to_string(fChannels[k]) + ": DCOffset = " + Num(fDCOffset[k]) +
                        " %, gain = " + Num(fGain[k]) + " dB, " + Num(fAdcToVolts[k] * 1e3, 5) +
                        " mV/count");
    Log::OutSummary("→ Window " + std::to_string(fRecordLength) + " samples (" +
                    Num(fRecordLength * fSamplingNs) + " ns), pre-trigger " +
                    std::to_string(fPreTrigger) + " samples; trigger " + fTriggerSourceFelib +
                    (fSoftwareTrigger ? " at " + Num(fSWTriggerRate) + " Hz" : "") +
                    "; polarity " + fPolarity + "; I/O " + fIOLevel);

    // Endpoint dei dati decodificati del firmware Scope.
    if (CAEN_FELib_GetHandle(fDev, "/endpoint/scope", &fEp) != CAEN_FELib_Success)
        Fail("Endpoint /endpoint/scope");
    uint64_t cartella;
    if (CAEN_FELib_GetParentHandle(fEp, nullptr, &cartella) != CAEN_FELib_Success)
        Fail("Endpoint folder");
    if (CAEN_FELib_SetValue(cartella, "/par/ActiveEndpoint", "scope") != CAEN_FELib_Success)
        Fail("ActiveEndpoint = scope");
    const char* formato =
        "[{\"name\":\"TIMESTAMP\",\"type\":\"U64\"},"
        "{\"name\":\"TRIGGER_ID\",\"type\":\"U32\"},"
        "{\"name\":\"WAVEFORM\",\"type\":\"U16\",\"dim\":2},"
        "{\"name\":\"WAVEFORM_SIZE\",\"type\":\"SIZE_T\",\"dim\":1},"
        "{\"name\":\"EVENT_SIZE\",\"type\":\"SIZE_T\"}]";
    if (CAEN_FELib_SetReadDataFormat(fEp, formato) != CAEN_FELib_Success)
        Fail("SetReadDataFormat");
}

// ===========================================================================
//  File di uscita
// ===========================================================================

void X2730::PrepareOutput() {
    if (!std::filesystem::exists(fOutputDir)) {
        Log::OutError("Output directory does not exist: " + fOutputDir);
        exit(1);
    }
    // Stessa regola del V1742 (Digitizer::PrepareOutput): il numero viene
    // dai file di dati col prefisso, le quattro cifre subito dopo; la
    // scheda va DOPO il numero, cosi' le schede della stessa run lo
    // condividono. Una run di prova si chiama TEST_ e non consuma numeri.
    const std::string base = fSaveData ? fOutputFile : std::string("TEST");
    int maxRun = -1;
    for (const auto& e : std::filesystem::directory_iterator(fOutputDir)) {
        if (!e.is_regular_file()) continue;
        const std::string n = e.path().filename().string();
        if (n.rfind(base + "_", 0) != 0 || n.find(".h5") == std::string::npos) continue;
        if (n.size() < base.size() + 5) continue;
        const std::string r = n.substr(base.size() + 1, 4);
        if (std::all_of(r.begin(), r.end(), ::isdigit)) maxRun = std::max(maxRun, std::stoi(r));
    }
    fRunNumber = fConfig.ForcedRunNumber() >= 0 ? fConfig.ForcedRunNumber() : maxRun + 1;
    const int postTrigger = static_cast<int>(std::lround(
        100.0 * (fRecordLength - fPreTrigger) / std::max<uint32_t>(1, fRecordLength)));
    const int msps = static_cast<int>(std::lround(1000.0 / fSamplingNs));
    auto Nome = [&](int run) {
        std::ostringstream s;
        s << fOutputDir << "/" << base << "_" << std::setw(4) << std::setfill('0') << run
          << "_" << fBoard << "_" << msps << "Ms_" << postTrigger << "PT.h5";
        return s.str();
    };
    fOutputPath = Nome(fRunNumber);
    while (std::filesystem::exists(fOutputPath) || std::filesystem::exists(fOutputPath + ".gz")) {
        if (fConfig.ForcedRunNumber() >= 0) {
            Log::OutError("Run file already exists: " + fOutputPath + " (run number imposed). Abort.");
            exit(1);
        }
        fOutputPath = Nome(++fRunNumber);
    }
    if (!fSaveData)
        Log::OutWarning("SaveData = false: RUN DI PROVA. Il file si chiama TEST_..., e a fine "
                        "run viene CANCELLATO (resta solo il .log).");
    Log::OutSummary("→ HDF5 output path selected: " + fOutputPath);

    const std::string logpath = fOutputPath.substr(0, fOutputPath.size() - 3) + ".log";
    Log::OpenFile(logpath);
    Log::FileOnly("\n===== configurazione usata (" + fConfig.GetFileName() + ") =====");
    Log::FileOnly(fConfig.AsString());
    Log::FileOnly("===== fine configurazione =====\n");

    try {
        H5::FileAccPropList fapl;
        fapl.setLibverBounds(H5F_LIBVER_LATEST, H5F_LIBVER_LATEST);   // richiesto da SWMR
        fFile = new H5::H5File(fOutputPath, H5F_ACC_TRUNC, H5::FileCreatPropList::DEFAULT, fapl);
        fFile->createGroup("/events");
        H5::Group h = fFile->createGroup("/config");

        fWidth = static_cast<hsize_t>(fChannels.size()) * fRecordLength;
        {
            hsize_t dims[2] = {0, fWidth}, maxd[2] = {H5S_UNLIMITED, fWidth}, chunk[2] = {kChunkEvents, fWidth};
            H5::DataSpace sp(2, dims, maxd);
            H5::DSetCreatPropList p;
            p.setChunk(2, chunk);
            fWaveforms = new H5::DataSet(fFile->createDataSet("/events/waveforms",
                                                              H5::PredType::NATIVE_INT16, sp, p));
        }
        auto Info = [&](const std::string& path, const H5::PredType& t) {
            hsize_t dims[1] = {0}, maxd[1] = {H5S_UNLIMITED}, chunk[1] = {256};
            H5::DataSpace sp(1, dims, maxd);
            H5::DSetCreatPropList p;
            p.setChunk(1, chunk);
            return new H5::DataSet(fFile->createDataSet(path, t, sp, p));
        };
        fCounter   = Info("/events/event_counter", H5::PredType::NATIVE_UINT32);
        fTimestamp = Info("/events/timestamp", H5::PredType::NATIVE_UINT64);
        {
            const double unita = 8.0;   // ns per conteggio del timestamp (scheda tecnica, firmware Scope)
            fTimestamp->createAttribute("UnitNs", H5::PredType::NATIVE_DOUBLE, H5::DataSpace())
                .write(H5::PredType::NATIVE_DOUBLE, &unita);
        }

        auto AttrU = [&](const char* n, uint32_t v) {
            h.createAttribute(n, H5::PredType::NATIVE_UINT, H5::DataSpace()).write(H5::PredType::NATIVE_UINT, &v);
        };
        auto AttrI = [&](const char* n, int v) {
            h.createAttribute(n, H5::PredType::NATIVE_INT, H5::DataSpace()).write(H5::PredType::NATIVE_INT, &v);
        };
        auto AttrD = [&](const char* n, double v) {
            h.createAttribute(n, H5::PredType::NATIVE_DOUBLE, H5::DataSpace()).write(H5::PredType::NATIVE_DOUBLE, &v);
        };
        auto AttrS = [&](const char* n, const std::string& v) {
            H5::StrType st(H5::PredType::C_S1, H5T_VARIABLE);
            h.createAttribute(n, st, H5::DataSpace()).write(st, v);
        };
        auto AttrVU = [&](const char* n, const std::vector<uint32_t>& v) {
            hsize_t d = v.size();
            H5::DataSpace sp(1, &d);
            h.createAttribute(n, H5::PredType::NATIVE_UINT, sp).write(H5::PredType::NATIVE_UINT, v.data());
        };
        auto AttrVD = [&](const char* n, const std::vector<double>& v) {
            hsize_t d = v.size();
            H5::DataSpace sp(1, &d);
            h.createAttribute(n, H5::PredType::NATIVE_DOUBLE, sp).write(H5::PredType::NATIVE_DOUBLE, v.data());
        };

        AttrD("StartTime", static_cast<double>(std::time(nullptr)));
        AttrI("RunNumber", fRunNumber);
        AttrU("RecordLength", fRecordLength);
        AttrU("PostTriggerSize", static_cast<uint32_t>(postTrigger));
        AttrU("PreTriggerSamples", fPreTrigger);
        AttrI("TransparentMode", 0);
        AttrI("FormatVersion", kFormatVersion);
        AttrU("TailCut", 0);
        AttrU("SamplesPerChannel", fRecordLength);
        AttrD("SamplingTime", fSamplingNs);
        AttrS("SamplingRate", std::to_string(msps) + "MHz");
        AttrS("Link", fUrl);
        AttrS("OutputFormat", "HDF5");
        AttrS("TriggerMode", fTriggerMode);
        AttrS("Board", fBoard);
        AttrU("BoardSerial", fSerial);
        AttrS("BoardModel", fModel);
        AttrS("FirmwareType", fFwType);
        AttrS("FirmwareVersion", fFpgaFw);
        AttrU("ADCBits", fAdcBits);
        AttrS("Polarity", fPolarity);
        AttrVU("ChannelList", fChannels);
        AttrVD("DCOffsetPercent", fDCOffset);
        AttrVD("GainDb", fGain);
        AttrVD("ADCToVolts", fAdcToVolts);
        if (fSelfTrigger) {
            AttrS("SelfTriggerMode", fSelfLogic);
            AttrVU("SelfTriggerChannels", fSelfChannels);
            std::vector<uint32_t> thr(fSelfThreshold.begin(), fSelfThreshold.end());
            AttrVU("SelfTriggerThreshold", thr);
        }

        // Da qui SWMR vieta di creare oggetti nuovi nel file: e' tutto sopra.
        if (H5Fstart_swmr_write(fFile->getId()) < 0) {
            Log::OutWarning("Cannot enable SWMR: the file will only be readable after the run ends.");
        } else {
            fSwmr = true;
            Log::OutSummary("→ SWMR enabled: the file can be read while the run is ongoing.");
        }
    } catch (const H5::Exception& e) {
        Log::OutError("HDF5 file creation failed: " + std::string(e.getDetailMsg()));
        exit(1);
    }
}

void X2730::Flush() {
    for (H5::DataSet* d : {fWaveforms, fCounter, fTimestamp})
        if (d) H5Dflush(d->getId());
}

// ===========================================================================
//  Acquisizione
// ===========================================================================

void X2730::Acquire() {
    // La FELib vuole un buffer per OGNI canale della scheda, anche spento
    // (per quelli WAVEFORM_SIZE torna 0).
    std::vector<std::vector<uint16_t>> buf(fNumCh, std::vector<uint16_t>(fRecordLength));
    std::vector<uint16_t*> wave(fNumCh);
    for (uint32_t c = 0; c < fNumCh; ++c) wave[c] = buf[c].data();
    std::vector<size_t> nsamp(fNumCh, 0);
    std::vector<int16_t> riga(fWidth);

    if (CAEN_FELib_SendCommand(fDev, "/cmd/ArmAcquisition") != CAEN_FELib_Success) Fail("ArmAcquisition");
    if (CAEN_FELib_SendCommand(fDev, "/cmd/SwStartAcquisition") != CAEN_FELib_Success) Fail("SwStartAcquisition");
    Log::OutSummary("→ Acquisition started (trigger " + fTriggerSourceFelib + ")");

    // Trigger software a intervalli esponenziali, in un thread suo: il ciclo
    // di lettura resta libero. Come nel V1742: se resta indietro di oltre un
    // secondo riparte da adesso invece di recuperare con una raffica.
    std::atomic<bool> fermaSW{false};
    std::atomic<uint64_t> swInviati{0}, swFalliti{0};
    std::thread sw;
    if (fSoftwareTrigger && fSWTriggerRate > 0) {
        sw = std::thread([&] {
            std::mt19937_64 rng(std::random_device{}());
            std::exponential_distribution<double> intervallo(fSWTriggerRate);
            auto prossimo = std::chrono::steady_clock::now();
            while (!fermaSW) {
                auto ora = std::chrono::steady_clock::now();
                if (ora >= prossimo) {
                    if (ora - prossimo > std::chrono::seconds(1)) prossimo = ora;
                    if (CAEN_FELib_SendCommand(fDev, "/cmd/SendSwTrigger") == CAEN_FELib_Success) ++swInviati;
                    else ++swFalliti;
                    prossimo += std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                        std::chrono::duration<double>(intervallo(rng)));
                }
                std::this_thread::sleep_until(std::min(prossimo, ora + std::chrono::milliseconds(20)));
            }
        });
    }

    const auto t0 = std::chrono::steady_clock::now();
    auto ultimaFlush = t0, ultimaStampa = t0;
    uint64_t totale = 0, scartati = 0;
    int errori = 0;
    const int maxErrori = 20;

    while (true) {
        if (Stop::Requested()) {
            Log::OutSummary("→ Arresto richiesto: chiudo la run dopo " + std::to_string(totale) + " eventi.");
            break;
        }
        if (fNEvents > 0 && totale >= fNEvents) break;
        const auto ora = std::chrono::steady_clock::now();
        const double t = std::chrono::duration<double>(ora - t0).count();
        if (fMaxTime > 0 && t >= fMaxTime) {
            Log::OutSummary("→ MaxTime reached (" + Num(fMaxTime) + " s).");
            break;
        }
        if (ora - ultimaStampa > std::chrono::seconds(1)) {
            std::cout << "\r   events " << totale << "   " << std::fixed << std::setprecision(1)
                      << (t > 0 ? totale / t : 0.0) << " Hz   " << std::flush;
            ultimaStampa = ora;
        }

        uint64_t ts = 0;
        uint32_t id = 0;
        size_t dim = 0;
        const int r = CAEN_FELib_ReadData(fEp, 100, &ts, &id, wave.data(), nsamp.data(), &dim);
        if (r == CAEN_FELib_Timeout) continue;
        if (r == CAEN_FELib_Stop) {
            Log::OutWarning("→ The board stopped the acquisition by itself.");
            break;
        }
        if (r != CAEN_FELib_Success) {
            // Come nel V1742: un errore isolato non chiude la run, una serie
            // si'. "giving up" e' la frase che riconosce notifiche.esito_run.
            ++errori;
            Log::OutWarning("ReadData failed (" + std::to_string(r) + "): " + LastError());
            if (errori >= maxErrori) {
                Log::OutError("ReadData failed " + std::to_string(errori) +
                              " times in a row: giving up. The link to the board is down.");
                break;
            }
            continue;
        }
        errori = 0;

        bool dimensioneGiusta = true;
        for (auto c : fChannels)
            if (nsamp[c] != fRecordLength) dimensioneGiusta = false;
        if (!dimensioneGiusta) {
            if (++scartati <= 5)
                Log::OutWarning("Event " + std::to_string(totale) + " has unexpected size: skipped.");
            continue;
        }
        for (size_t k = 0; k < fChannels.size(); ++k) {
            const uint16_t* w = wave[fChannels[k]];
            std::copy(w, w + fRecordLength, riga.begin() + k * fRecordLength);
        }
        try {
            hsize_t nd2[2] = {fRows + 1, fWidth};
            fWaveforms->extend(nd2);
            H5::DataSpace fs = fWaveforms->getSpace();
            hsize_t st2[2] = {fRows, 0}, ct2[2] = {1, fWidth};
            fs.selectHyperslab(H5S_SELECT_SET, ct2, st2);
            H5::DataSpace ms(2, ct2);
            fWaveforms->write(riga.data(), H5::PredType::NATIVE_INT16, ms, fs);

            hsize_t nd1[1] = {fRows + 1}, st1[1] = {fRows}, ct1[1] = {1};
            H5::DataSpace m1(1, ct1);
            fCounter->extend(nd1);
            H5::DataSpace f1 = fCounter->getSpace();
            f1.selectHyperslab(H5S_SELECT_SET, ct1, st1);
            fCounter->write(&id, H5::PredType::NATIVE_UINT32, m1, f1);
            fTimestamp->extend(nd1);
            H5::DataSpace f2 = fTimestamp->getSpace();
            f2.selectHyperslab(H5S_SELECT_SET, ct1, st1);
            fTimestamp->write(&ts, H5::PredType::NATIVE_UINT64, m1, f2);
            ++fRows;
        } catch (const H5::Exception& e) {
            Log::OutError("HDF5 write error: " + std::string(e.getDetailMsg()));
        }
        ++totale;

        // Flush a conteggio e a tempo, come il V1742: a rate basso il monitor
        // non aspetta LiveFlushEvery eventi per vedere qualcosa.
        if (fSwmr) {
            const auto adesso = std::chrono::steady_clock::now();
            if (fRows % fFlushEvery == 0 ||
                std::chrono::duration_cast<std::chrono::milliseconds>(adesso - ultimaFlush).count() >=
                    kFlushMaxDelayMs) {
                Flush();
                ultimaFlush = adesso;
            }
        }
    }
    const double durata = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();

    fermaSW = true;
    if (sw.joinable()) sw.join();
    CAEN_FELib_SendCommand(fDev, "/cmd/SwStopAcquisition");
    CAEN_FELib_SendCommand(fDev, "/cmd/DisarmAcquisition");

    std::cout << std::endl << std::endl;
    if (fSoftwareTrigger)
        Log::OutSummary("→ Software triggers sent: " + std::to_string(swInviati.load()) +
                        " (failed: " + std::to_string(swFalliti.load()) + "), events recorded: " +
                        std::to_string(totale));
    // Le stesse righe del V1742: controllore, riepilogo e mail le cercano
    // per sapere quanti eventi e quanto tempo.
    Log::OutSummary("Acquisition complete.");
    Log::OutSummary("→ Total events recorded: " + std::to_string(totale));
    Log::OutSummary("→ Acquisition time: " + std::to_string(durata) + " s");
    Log::OutSummary("→ Trigger rate: " + std::to_string(durata > 0 ? totale / durata / 1000.0 : 0.0) + " kHz");
    Log::OutSummary("→ Events written to file: " + std::to_string(fRows));
    if (scartati > 0)
        Log::OutWarning("→ Events skipped (unexpected size): " + std::to_string(scartati));
    LogBoardStatus("at end");
}

// ===========================================================================
//  Chiusura
// ===========================================================================

void X2730::CloseOutputFile() {
    try {
        for (H5::DataSet** d : {&fWaveforms, &fCounter, &fTimestamp})
            if (*d) {
                (*d)->close();
                delete *d;
                *d = nullptr;
            }
        if (fFile) {
            fFile->close();
            delete fFile;
            fFile = nullptr;
        }
    } catch (const H5::Exception& e) {
        Log::OutError("→ HDF5 file close failed: " + std::string(e.getDetailMsg()));
        return;
    }
    if (!fSaveData) {
        std::filesystem::remove(fOutputPath);
        Log::OutSummary("→ Run di prova (SaveData = false): file dei dati cancellato, resta il .log.");
        return;
    }
    // Non si comprime qui: lo fa tools/archivia.py, come per il V1742 con
    // CompressAtEnd = false, che riconosce dal superblocco i file chiusi bene.
    Log::OutSummary("→ File HDF5 chiuso, non compresso: lo comprime tools/archivia.py.");
}

void X2730::Close() {
    if (fFile) CloseOutputFile();
    if (fOpen) {
        CAEN_FELib_Close(fDev);
        fOpen = false;
    }
}
