// Digitizer.cpp aggiornato e corretto per V1742 32 canali
#include <filesystem>
#define CAEN_USE_X742

#include <vector>
#include <sys/time.h>
#include <ctime>
#include <cmath>
#include <chrono>
#include <fstream>
#include <iomanip>
#include <numeric>
#include <map>
#include <string>
#include <sstream>
#include <thread>
#include <iostream>
#include <algorithm>   // <-- per std::all_of, std::remove_if
#include <cstdio>      // <-- per std::sscanf
#include <sys/stat.h>  // <-- per stat() sul file delle soglie live

#include "Digitizer.h"
#include "Log.h"
#include <CAENDigitizer.h>
#include <CAENDigitizerType.h>
#include <H5Cpp.h>

using namespace H5;

// Un decimale, senza gli zeri inutili di std::to_string
static std::string FmtOffset(double v) {
    std::ostringstream os;
    os << std::fixed << std::setprecision(1) << v;
    std::string s = os.str();
    if (s.size() > 2 && s.substr(s.size() - 2) == ".0") s.erase(s.size() - 2);
    return s;
}


// =============================================================
//  CTOR
// =============================================================
Digitizer::Digitizer()
    : fConfig(Config::GetInstance()),
      fIsRunning(true),
      fAcqRunning(false),
      fConnectionType(CAEN_DGTZ_ETH_V4718),
      fIPAddress(fConfig.GetEntry<std::string>("digitizer","IPAddress","192.168.99.105")),
      fConetNode(fConfig.GetEntry<int>("digitizer","ConetNode",0)),
      fVMEBaseAddress(0x32100000),
      fHandle(0),
      fRecordLength(fConfig.GetEntry<uint32_t>("digitizer","RecordLength",1024)),
      fNChannels(32), // V1742 32-ch
      fChannelMask(0),
      fNActiveChannels(0),
      fPostTriggerSize(fConfig.GetEntry<uint32_t>("digitizer","PostTriggerSize",50)),
      fTailCut(fConfig.GetEntry<uint32_t>("digitizer","TailCut",8)),
      fAcquisitionMode(CAEN_DGTZ_FIRST_TRG_CONTROLLED),
      fNTransferedEvents(1),
      fGroupMask(0),
      fSelfTrigger(fConfig.GetEntry<bool>("digitizer","SelfTrigger",false)),
      fSaveRaw(fConfig.GetEntry<bool>("digitizer","SaveRaw",false)),
      fExternalTrigger(fConfig.GetEntry<bool>("digitizer","ExternalTrigger",true)),
      fSelfTriggerMode(CAEN_DGTZ_TRGMODE_DISABLED),
      fExternalTriggerMode(CAEN_DGTZ_TRGMODE_ACQ_ONLY),
      fPulsePolarity(static_cast<CAEN_DGTZ_PulsePolarity_t>(
          fConfig.GetEntry<uint32_t>("digitizer","PulsePolarity",1))),
      fTriggerPolarity(static_cast<CAEN_DGTZ_TriggerPolarity_t>(
          fConfig.GetEntry<uint32_t>("digitizer","TriggerPolarity",1))),
      fNRMSThreshold(fConfig.GetEntry<double>("digitizer","NRMSThreshold",3.0)),
      fIntegralThreshold(fConfig.GetEntry<double>("digitizer","IntegralThreshold",-100.0)),
      fBuffer(nullptr),
      fBufferSize(0),
      fEventInfo(),
      fVoidEvent(nullptr),
      fEvent(nullptr),
      fEventPtr(nullptr),
      fWaitTimeS(fConfig.GetEntry<double>("digitizer","WaitTimeS",3.0)),
      fSamplingTime(0.2e-9),
      fSamplingRateStr(fConfig.GetEntry<std::string>("digitizer","SamplingRate","5GHz")),
      fACQT(),
      fDeadT(0.0),
      fNNoiseEvents(fConfig.GetEntry<uint32_t>("digitizer","NNoiseEvents",100)),
      fNEvents(fConfig.GetEntry<uint32_t>("digitizer","NEvents",100)),
      fDuration(0),
      fOutputFormat(kHDF5),
      fOutputDir(fConfig.GetEntry<std::string>("digitizer","OutputDir","")),
      fOutputFileName(fConfig.GetEntry<std::string>("digitizer","OutputFile","run")),
      fRunNumber(fConfig.GetEntry<int>("digitizer","RunNumber",1)),
      fASCIIFile(),
//      fAcqRunning(false),
      fTimestamp_s(0),
      fTimestamp_ns(0),
      fTriggerTime(0),
      fSelfTriggerModeStr(fConfig.GetEntry<std::string>("digitizer","SelfTriggerMode","paired")),
      fSelfTriggerGlobal(false),
      fSelfTriggerThresholdMode(fConfig.GetEntry<std::string>("digitizer","SelfTriggerThresholdMode","absolute")),
      fSelfTriggerRelative(false),
      fSelfTriggerChannels(),
      fSelfTriggerThreshold(),
      fSelfTriggerThresholdOffset(fConfig.GetEntry<double>("digitizer","SelfTriggerThresholdOffset",100.0)),
      fSelfTriggerOffset(),
      fTransparentBaseline(),
      fTransparentRMS(),
      fLiveMonitoringCfg(fConfig.GetEntry<bool>("digitizer","LiveMonitoring",true)),
      fFlushEveryCfg(fConfig.GetEntry<uint32_t>("digitizer","LiveFlushEvery",10)),
      fLiveThresholdFileCfg(fConfig.GetEntry<std::string>("digitizer","LiveThresholdFile","")),
      fTransparentDump(fConfig.GetEntry<bool>("digitizer","TransparentDump",false)),
      fTransparentDumpEvents(fConfig.GetEntry<uint32_t>("digitizer","TransparentDumpEvents",200)),
      fTransparentDumpTrigger(fConfig.GetEntry<std::string>("digitizer","TransparentDumpTrigger","software"))
{
    // I puntatori HDF5 non erano inizializzati: PrepareOutput li assegna, ma
    // CloseOutputFile e AcquireEvents li controllano contro nullptr.
    fH5File         = nullptr;
    fH5Group        = nullptr;
    fH5Waveforms    = nullptr;
    fH5WaveformsRaw = nullptr;
    fH5Rows         = 0;
    fEventWidth     = 0;
    fSkippedEvents  = 0;
    fLiveMonitoring = fLiveMonitoringCfg;
    fFlushEvery     = (fFlushEveryCfg == 0) ? 1 : fFlushEveryCfg;

    fLiveThresholdPath = fLiveThresholdFileCfg.empty()
                           ? fOutputDir + "/live-threshold.txt"
                           : fLiveThresholdFileCfg;
    fStatusPath        = fOutputDir + "/live-status.json";
    fLiveThresholdMtime = 0;
    fThresholdGen       = 0;
    fThresholdGenRow    = 0;

    // ===== Read ChannelList dal TOML =====
    std::vector<int64_t> tmpchlist =
        fConfig.GetEntryList<int64_t>("digitizer","ChannelList",-1,0);

    if (tmpchlist.empty() || tmpchlist[0] == -1) {
        Log::OutError("No valid ChannelList provided.");
        exit(1);
    }

    for (auto c : tmpchlist) {
        if (c < 0 || c >= 32) {
            Log::OutError("Invalid channel index " + std::to_string(c) +
                          " (must be 0–31 for V1742 32-ch)");
            exit(1);
        }
        fChannelList.push_back(static_cast<uint32_t>(c));
    }

    fNActiveChannels = fChannelList.size();
    fChannelMask = 0;
    for (auto ch : fChannelList)
        fChannelMask |= (1u << ch);

    Log::OutSummary("→ Active channels: " + std::to_string(fNActiveChannels));
    for (auto ch : fChannelList)
        Log::OutSummary("   ch" + std::to_string(ch));

    std::string format =
        fConfig.GetEntry<std::string>("digitizer","OutputFormat","HDF5");
    Log::OutSummary("OutputFormat read from config = '" + format + "'");
    if (format == "ASCII") fOutputFormat = kASCII;
    else if (format == "HDF5")  fOutputFormat = kHDF5;
    else {
        Log::OutError("Unknown output format: " + format);
        exit(1);
    }

    if (fTransparentDump &&
        fTransparentDumpTrigger != "software" && fTransparentDumpTrigger != "self") {
        Log::OutError("Unknown TransparentDumpTrigger: '" + fTransparentDumpTrigger +
                      "' (valid: \"software\", \"self\")");
        exit(1);
    }

    if (fTransparentDump) {
        fOutputFileName += "_TM";       // numerazione separata dalle run di fisica
        Log::OutSummary("→ TRANSPARENT MODE DUMP: " +
                        std::to_string(fTransparentDumpEvents) +
                        " events, no acquisition will follow.");
    }

    // ===== Self-trigger (auto-trigger dai canali di input) =====
    if (fSelfTriggerModeStr == "global")      fSelfTriggerGlobal = true;
    else if (fSelfTriggerModeStr == "paired") fSelfTriggerGlobal = false;
    else {
        Log::OutError("Unknown SelfTriggerMode: '" + fSelfTriggerModeStr +
                      "' (valid: \"paired\", \"global\")");
        exit(1);
    }

    if (fSelfTriggerThresholdMode == "relative")      fSelfTriggerRelative = true;
    else if (fSelfTriggerThresholdMode == "absolute") fSelfTriggerRelative = false;
    else {
        Log::OutError("Unknown SelfTriggerThresholdMode: '" + fSelfTriggerThresholdMode +
                      "' (valid: \"absolute\", \"relative\")");
        exit(1);
    }

    // Canali che partecipano all'OR di trigger: default = tutti quelli acquisiti
    std::vector<int64_t> tmpstchlist =
        fConfig.GetEntryList<int64_t>("digitizer","SelfTriggerChannels",-1,0);

    if (tmpstchlist.empty() || tmpstchlist[0] == -1) {
        fSelfTriggerChannels = fChannelList;
    } else {
        for (auto c : tmpstchlist) {
            if (c < 0 || c >= static_cast<int64_t>(fNChannels)) {
                Log::OutError("Invalid SelfTriggerChannels index " + std::to_string(c) +
                              " (must be 0-31 for V1742 32-ch)");
                exit(1);
            }
            if (std::find(fChannelList.begin(), fChannelList.end(),
                          static_cast<uint32_t>(c)) == fChannelList.end())
                Log::OutWarning("SelfTriggerChannels: ch" + std::to_string(c) +
                                " triggers but is not in ChannelList (it will not be saved).");
            fSelfTriggerChannels.push_back(static_cast<uint32_t>(c));
        }
    }

    // Soglie assolute: una per canale di self-trigger, l'ultima viene replicata
    std::vector<int64_t> thrlist =
        fConfig.GetEntryList<int64_t>("digitizer","SelfTriggerThreshold",
                                      static_cast<int64_t>(2000),
                                      fSelfTriggerChannels.size());
    for (size_t i = 0; i < fSelfTriggerChannels.size() && i < thrlist.size(); ++i) {
        int64_t thr = thrlist[i];
        if (thr < 0 || thr > static_cast<int64_t>(MAX_THRESHOLD_COUNTS)) {
            Log::OutError("SelfTriggerThreshold for ch" +
                          std::to_string(fSelfTriggerChannels[i]) + " = " + std::to_string(thr) +
                          " out of range (0-" + std::to_string(MAX_THRESHOLD_COUNTS) + ")");
            exit(1);
        }
        fSelfTriggerThreshold[fSelfTriggerChannels[i]] = static_cast<uint32_t>(thr);
    }

    // Offset per canale: la chiave accetta sia uno scalare, applicato a tutti i
    // canali, sia una lista con un valore per canale (l'ultimo viene replicato).
    {
        toml::array* offarr =
            fConfig.GetTbl()["digitizer"]["SelfTriggerThresholdOffset"].as_array();

        // value<double>() converte anche gli interi, mentre get_as<int64_t>()
        // su un valore decimale restituisce nullptr: dereferenziarlo era un
        // segfault silenzioso.
        std::vector<double> offlist;
        if (offarr != nullptr) {
            for (size_t i = 0; i < offarr->size(); ++i) {
                auto v = offarr->get(i)->value<double>();
                if (!v) {
                    Log::OutError("SelfTriggerThresholdOffset: element " +
                                  std::to_string(i) + " is not a number.");
                    exit(1);
                }
                offlist.push_back(*v);
            }
            if (offlist.empty()) {
                Log::OutError("SelfTriggerThresholdOffset: empty list.");
                exit(1);
            }
            // l'ultimo valore vale per i canali rimanenti
            while (offlist.size() < fSelfTriggerChannels.size())
                offlist.push_back(offlist.back());
        }

        for (size_t i = 0; i < fSelfTriggerChannels.size(); ++i) {
            double off = (offarr != nullptr && i < offlist.size())
                            ? offlist[i]
                            : fSelfTriggerThresholdOffset;

            if (off < 0.0 || off > static_cast<double>(MAX_THRESHOLD_COUNTS)) {
                Log::OutError("SelfTriggerThresholdOffset for ch" +
                              std::to_string(fSelfTriggerChannels[i]) + " = " +
                              std::to_string(off) + " out of range (0-" +
                              std::to_string(MAX_THRESHOLD_COUNTS) + ")");
                exit(1);
            }
            fSelfTriggerOffset[fSelfTriggerChannels[i]] = off;
        }
    }

    if (fSaveRaw)
        Log::OutWarning("SaveRaw = true duplicates identical data: the waveforms in "
                        "/events are already stored uncorrected, so /events_raw only "
                        "doubles the file size.");

    if (fSelfTrigger) {
        Log::OutSummary("→ Self-trigger ENABLED (mode = " + fSelfTriggerModeStr +
                        ", threshold = " + fSelfTriggerThresholdMode + ")");
        std::string chlist;
        for (auto ch : fSelfTriggerChannels)
            chlist += " ch" + std::to_string(ch);
        Log::OutSummary("   trigger channels:" + chlist);

        // Le due chiavi di soglia si escludono a vicenda: dire quale conta
        // evita di modificare quella sbagliata e non vedere alcun effetto.
        if (fSelfTriggerRelative) {
            std::string offlist;
            for (auto ch : fSelfTriggerChannels)
                offlist += " ch" + std::to_string(ch) + "=" +
                           FmtOffset(fSelfTriggerOffset[ch]);
            Log::OutSummary("   threshold driven by SelfTriggerThresholdOffset "
                            "[counts]:" + offlist);
            Log::OutWarning("   SelfTriggerThreshold is IGNORED in \"relative\" mode "
                            "(used only as fallback if the Transparent Mode "
                            "measurement fails).");
        } else {
            std::string thrlist;
            for (auto ch : fSelfTriggerChannels)
                thrlist += " ch" + std::to_string(ch) + "=" +
                           std::to_string(fSelfTriggerThreshold[ch]);
            Log::OutSummary("   threshold driven by SelfTriggerThreshold:" + thrlist);
            Log::OutWarning("   SelfTriggerThresholdOffset is IGNORED in "
                            "\"absolute\" mode.");
        }
    }
}

// =============================================================
//  DTOR
// =============================================================
Digitizer::~Digitizer() {
    Close();
}

// =============================================================
//  Close + cleanup CAEN
// =============================================================
void Digitizer::Close() {

    // Se un'acquisizione SW è stata avviata, fermala
    if (fAcqRunning && fHandle) {
        CAEN_DGTZ_SWStopAcquisition(fHandle);
        fAcqRunning = false;
    }

    if (fVoidEvent) {
        CAEN_DGTZ_FreeEvent(fHandle, &fVoidEvent);
        fVoidEvent = nullptr;
        fEvent     = nullptr;
        fEventPtr  = nullptr;
    }

    if (fBuffer) {
        CAEN_DGTZ_FreeReadoutBuffer(&fBuffer);
        fBuffer     = nullptr;
        fBufferSize = 0;
    }

    if (fHandle) {
        CAEN_DGTZ_CloseDigitizer(fHandle);
        fHandle = 0;
    }
}

// =============================================================
//  SELECT BOARD
// =============================================================
void Digitizer::SelectBoard()
{
    CAEN_DGTZ_ErrorCode err = CAEN_DGTZ_OpenDigitizer2(
        fConnectionType,
        (void*)fIPAddress.c_str(),
        fConetNode,
        fVMEBaseAddress,
        &fHandle);

    if (err != CAEN_DGTZ_Success) {
        Log::OutError("Cannot connect. Code: " + std::to_string(err));
        exit(1);
    }

    Log::OutSummary("Digitizer connected.");

    CAEN_DGTZ_GetInfo(fHandle,&fBoardInfo);
    Log::OutSummary("Digitizer model: " + std::string(fBoardInfo.ModelName));
    Log::OutSummary("ROC firmware release: " + std::string(fBoardInfo.ROC_FirmwareRel));
    Log::OutSummary("AMC firmware release: " + std::string(fBoardInfo.AMC_FirmwareRel));

    uint32_t reported = fBoardInfo.Channels;
    Log::OutSummary("BoardInfo.Channels (raw) = " + std::to_string(reported));
    Log::OutSummary("→ Interpreting this as 4 groups × 8 channels = 32 channels total.");

    // fNChannels resta 32; fChannelList resta quella del TOML

    // === Sampling rate selection ===
    CAEN_DGTZ_DRS4Frequency_t freq = CAEN_DGTZ_DRS4_5GHz;
    double sTime = 0.2e-9;

    if (fSamplingRateStr == "5GHz") {
        freq = CAEN_DGTZ_DRS4_5GHz;    sTime = 0.2e-9;
    } else if (fSamplingRateStr == "2.5GHz") {
        freq = CAEN_DGTZ_DRS4_2_5GHz;  sTime = 0.4e-9;
    } else if (fSamplingRateStr == "1GHz") {
        freq = CAEN_DGTZ_DRS4_1GHz;    sTime = 1.0e-9;
    } else {
        Log::OutWarning("Unknown SamplingRate = '" + fSamplingRateStr + "'. Defaulting to 5 GHz.");
    }

    err = CAEN_DGTZ_SetDRS4SamplingFrequency(fHandle, freq);
    if (err == CAEN_DGTZ_Success) {
        fSamplingTime = sTime;
        Log::OutSummary("→ Sampling frequency set to " + fSamplingRateStr +
                        " (" + std::to_string(fSamplingTime * 1e9) + " ns per sample)");
    } else {
        Log::OutWarning("→ Failed to set DRS4 sampling frequency (code = " + std::to_string(err) +
                        "). Using default 5 GHz.");
        fSamplingTime = 0.2e-9;
    }

    // Carica correzioni DRS4
    err = CAEN_DGTZ_LoadDRS4CorrectionData(fHandle,freq);
    if (err == CAEN_DGTZ_Success) {
        Log::OutSummary("→ PLL / DRS4 calibration loaded.");
    } else {
        Log::OutWarning("→ PLL calibration not supported or failed (code = " + std::to_string(err) + ").");
    }

    // Le tabelle si caricano perche' la stessa chiamata porta con se' la
    // calibrazione del PLL, ma non vanno MAI applicate: riguardano il percorso
    // di memoria del DRS4, mentre il self-trigger deve vedere il segnale che
    // arriva dal rivelatore. Disabilitarle qui rende lo stato deterministico,
    // qualunque sia il tipo di trigger configurato dopo.
    if (CAEN_DGTZ_DisableDRS4Correction(fHandle) != CAEN_DGTZ_Success)
        Log::OutWarning("Cannot disable DRS4 corrections at startup.");

    CAEN_DGTZ_Calibrate(fHandle);
    Log::OutSummary("PLL calibration done.");
}

// =======================================================================
//  INIT ACQUISITION
// =======================================================================
void Digitizer::InitAcquisition()
{
    CAEN_DGTZ_ErrorCode err;

    err = CAEN_DGTZ_MallocReadoutBuffer(fHandle,&fBuffer,&fBufferSize);
    if (err != CAEN_DGTZ_Success) {
        Log::OutError("Cannot allocate readout buffer.");
        exit(1);
    }

    err = CAEN_DGTZ_AllocateEvent(fHandle,&fVoidEvent);
    if (err != CAEN_DGTZ_Success) {
        Log::OutError("Cannot allocate event structure.");
        exit(1);
    }

    fEvent = reinterpret_cast<CAEN_DGTZ_X742_EVENT_t*>(fVoidEvent);
    Log::OutSummary("Acquisition initialized.");
}

// =======================================================================
//  RESET
// =======================================================================
void Digitizer::Reset() {
    CAEN_DGTZ_ErrorCode re = CAEN_DGTZ_Reset(fHandle);
    if (re == CAEN_DGTZ_Success) {
        Log::OutSummary("Digitizer reset.");
    } else {
        Log::OutError("Cannot reset digitizer. Error code: " + std::to_string(re));
        exit(1);
    }
}

// =======================================================================
//  CONFIGURE
// =======================================================================
void Digitizer::Configure() {
    Log::OutSummary("Configuring digitizer parameters...");

    auto Check = [&](CAEN_DGTZ_ErrorCode e, const std::string& what) {
        if (e != CAEN_DGTZ_Success) {
            Log::OutWarning("Configure: " + what + " failed (code = " + std::to_string(e) + ")");
        }
    };

    // Record Length
    Check(CAEN_DGTZ_SetRecordLength(fHandle, fRecordLength), "SetRecordLength");

    // Per V1742 32ch: 4 gruppi × 8 canali
    const uint32_t channelsPerGroup = 8;
    uint32_t hwGroups = (fNChannels + channelsPerGroup - 1) / channelsPerGroup; // -> 4

    // Abilita solo i gruppi esistenti (0..3)
    fGroupMask = (1u << hwGroups) - 1;  // es: 4 gruppi -> 0x0F
    Check(CAEN_DGTZ_SetGroupEnableMask(fHandle, fGroupMask), "SetGroupEnableMask");

    // Canali abilitati (mask derivata da ChannelList)
//    Check(CAEN_DGTZ_SetChannelEnableMask(fHandle, fChannelMask), "SetChannelEnableMask");

    // PostTrigger
    Check(CAEN_DGTZ_SetPostTriggerSize(fHandle, fPostTriggerSize), "SetPostTriggerSize");

    // Modalità acquisizione
    Check(CAEN_DGTZ_SetAcquisitionMode(fHandle, CAEN_DGTZ_SW_CONTROLLED), "SetAcquisitionMode");

    // IO Level NIM
    Check(CAEN_DGTZ_SetIOLevel(fHandle, CAEN_DGTZ_IOLevel_NIM), "SetIOLevel");

    // profondità FIFO
//    Check(CAEN_DGTZ_SetMaxNumEventsBLT(fHandle, 2048), "SetMaxNumEventsBLT");

    // Trigger Polarity
    for (auto ch : fChannelList)
        Check(CAEN_DGTZ_SetTriggerPolarity(fHandle, ch, fTriggerPolarity),
              "SetTriggerPolarity ch" + std::to_string(ch));

    // Offset per ogni canale (segnali negativi)
    for (auto ch : fChannelList) {
        Check(CAEN_DGTZ_SetChannelDCOffset(fHandle, ch, 0x7000),
              "SetChannelDCOffset ch" + std::to_string(ch));
    }

    Log::OutSummary("Digitizer configuration complete.");
}

// =======================================================================
//  GET VME LIB VERSION
// =======================================================================
void Digitizer::GetVMElibVersion() {
    std::cout << "CAEN VMElib version: "
              << CAENVME_VERSION_MAJOR << "."
              << CAENVME_VERSION_MINOR << "."
              << CAENVME_VERSION_PATCH << std::endl;
}

// =======================================================================
//  MEASURE BASELINE  (SW trigger, media e RMS per canale)
// =======================================================================
bool Digitizer::MeasureBaseline(std::map<uint32_t,double>& mean,
                                std::map<uint32_t,double>& rms,
                                uint32_t ntriggers,
                                const std::string& tag)
{
    if (!fBuffer || !fVoidEvent) {
        Log::OutError("MeasureBaseline called before InitAcquisition().");
        return false;
    }

    // V1742 32-ch: 32 canali, 4 gruppi, 8 canali per gruppo
    const uint32_t channelsPerGroup = 8;
    uint32_t hwChannels = fNChannels;           // 32
    uint32_t hwGroups   = (hwChannels + channelsPerGroup - 1) / channelsPerGroup; // 4

    mean.clear();
    rms.clear();

    // Unione dei canali acquisiti e di quelli che generano il self-trigger:
    // questi ultimi possono non comparire in ChannelList ma serve comunque la
    // loro baseline per fissare la soglia.
    std::vector<uint32_t> channels = fChannelList;
    for (auto ch : fSelfTriggerChannels)
        if (std::find(channels.begin(), channels.end(), ch) == channels.end())
            channels.push_back(ch);

    std::map<uint32_t,double> sum, sum2;
    std::map<uint32_t,uint64_t> nsum;

    CAEN_DGTZ_ErrorCode re;

    re = CAEN_DGTZ_SWStartAcquisition(fHandle);
    if (re != CAEN_DGTZ_Success) {
        Log::OutError("Start acquisition failed in MeasureBaseline (" + tag +
                      "). Code: " + std::to_string(re));
        return false;
    }
    fAcqRunning = true;

    Log::OutDebug("MeasureBaseline[" + tag + "]: hwChannels=" +
                  std::to_string(hwChannels) +
                  ", hwGroups=" + std::to_string(hwGroups) +
                  ", channelsPerGroup=" + std::to_string(channelsPerGroup));

    auto Stop = [&]() {
        CAEN_DGTZ_SWStopAcquisition(fHandle);
        fAcqRunning = false;
    };

    for (uint32_t t = 0; t < ntriggers; ++t) {

        re = CAEN_DGTZ_SendSWtrigger(fHandle);
        if (re != CAEN_DGTZ_Success) {
            Log::OutError("Software trigger failed in MeasureBaseline (" + tag +
                          "). Code: " + std::to_string(re));
            Stop();
            return false;
        }

        re = CAEN_DGTZ_ReadData(
            fHandle,
            CAEN_DGTZ_SLAVE_TERMINATED_READOUT_MBLT,
            fBuffer,
            &fBufferSize
        );
        if (re != CAEN_DGTZ_Success) {
            Log::OutError("ReadData failed in MeasureBaseline (" + tag +
                          "). Code: " + std::to_string(re));
            Stop();
            return false;
        }
        if (fBufferSize == 0) {
            Log::OutError("ReadData returned empty buffer in MeasureBaseline (" + tag + ").");
            Stop();
            return false;
        }

        uint32_t nEvents = 0;
        re = CAEN_DGTZ_GetNumEvents(fHandle, fBuffer, fBufferSize, &nEvents);
        if (re != CAEN_DGTZ_Success) {
            Log::OutError("GetNumEvents failed in MeasureBaseline (" + tag +
                          "). Code: " + std::to_string(re));
            Stop();
            return false;
        }

        for (uint32_t i = 0; i < nEvents; ++i) {
            re = CAEN_DGTZ_GetEventInfo(
                fHandle, fBuffer, fBufferSize,
                i, &fEventInfo, &fEventPtr
            );
            if (re != CAEN_DGTZ_Success || !fEventPtr) {
                Log::OutError("GetEventInfo failed for event " + std::to_string(i) +
                              ". Code: " + std::to_string(re));
                continue;
            }

            re = CAEN_DGTZ_DecodeEvent(fHandle, fEventPtr, &fVoidEvent);
            if (re != CAEN_DGTZ_Success || !fVoidEvent) {
                Log::OutError("DecodeEvent failed for event " + std::to_string(i) +
                              ". Code: " + std::to_string(re));
                continue;
            }

            fEvent = reinterpret_cast<CAEN_DGTZ_X742_EVENT_t*>(fVoidEvent);

            for (uint32_t ch : channels) {
                if (ch >= hwChannels) continue;

                int group    = ch / channelsPerGroup;    // 8 ch per gruppo
                int local_ch = ch % channelsPerGroup;    // 0..7

                if (group < 0 || (uint32_t)group >= hwGroups)
                    continue;

                if (fEvent->GrPresent[group] == 0)
                    continue;

                uint32_t nsamples = fEvent->DataGroup[group].ChSize[local_ch];
                float* waveform   = fEvent->DataGroup[group].DataChannel[local_ch];

                if (nsamples < MIN_SAMPLES || nsamples > MAX_SAMPLES || waveform == nullptr) {
                    Log::OutDebug("  → ch" + std::to_string(ch) +
                                  " skip in baseline (nsamples=" + std::to_string(nsamples) + ")");
                    continue;
                }

                for (uint32_t k = 0; k < nsamples; ++k) {
                    sum[ch]  += waveform[k];
                    sum2[ch] += static_cast<double>(waveform[k]) * waveform[k];
                }
                nsum[ch] += nsamples;
            }
        }
    }

    Stop();

    for (auto& it : nsum) {
        if (it.second == 0) continue;
        uint32_t ch = it.first;
        double n    = static_cast<double>(it.second);
        double m    = sum[ch] / n;
        double var  = sum2[ch] / n - m * m;
        mean[ch] = m;
        rms[ch]  = (var > 0.0) ? std::sqrt(var) : 0.0;

        Log::OutDebug("  → ch" + std::to_string(ch) +
                      " [" + tag + "] baseline = " + std::to_string(m) +
                      ", rms = " + std::to_string(rms[ch]) +
                      " (nsamples=" + std::to_string(it.second) + ")");
    }

    return !mean.empty();
}

// =======================================================================
//  SET TRIGGER THRESHOLD  (BASELINE CALCULATION, Output Mode)
// =======================================================================
void Digitizer::SetTriggerThreshold(double offset) {
    (void)offset;

    Log::OutSummary("Calculating channels baseline...");

    std::map<uint32_t,double> rms;
    if (!MeasureBaseline(fBaselineMean, rms, 1, "output"))
        return;

    Log::OutSummary("Baseline calculation completed.");
}

// =======================================================================
//  TRANSPARENT MODE  (0x8000 bit[13])
// =======================================================================
void Digitizer::SetTransparentMode(bool enable) {
    // In Transparent Mode il DRS4 continua a campionare e l'ADC legge in
    // continuazione: e' il dominio in cui lavora la logica di self-trigger,
    // quindi e' qui che vanno misurate le baseline per fissare le soglie.
    uint32_t reg = enable ? REG_BOARD_CONFIG_SET : REG_BOARD_CONFIG_CLEAR;
    CAEN_DGTZ_ErrorCode re =
        CAEN_DGTZ_WriteRegister(fHandle, reg, 1u << BIT_TRANSPARENT_MODE);
    if (re != CAEN_DGTZ_Success)
        Log::OutWarning(std::string("Cannot ") + (enable ? "enable" : "disable") +
                        " Transparent Mode (code = " + std::to_string(re) + ")");
    else
        Log::OutDebug(std::string("Transparent Mode ") + (enable ? "ENABLED" : "DISABLED"));
}

// =======================================================================
//  SELF-TRIGGER: calcolo soglie
// =======================================================================
void Digitizer::ComputeSelfTriggerThresholds() {

    if (!fSelfTriggerRelative) {
        // Soglie assolute: gia' lette dal TOML nel costruttore.
        return;
    }

    // Modo "relative": misura la baseline in Transparent Mode e posiziona la
    // soglia a fSelfTriggerThresholdOffset conteggi dal piedistallo, nel verso
    // indicato da TriggerPolarity.
    Log::OutSummary("Measuring Transparent Mode baseline for self-trigger thresholds...");

    // Le correzioni DRS4 restano spente, e non vengono riaccese dopo: con il
    // self-trigger il comparatore lavora sul segnale che arriva dal rivelatore,
    // e le tabelle riguardano solo il percorso di memoria del DRS4. Applicarle
    // qui non cambierebbe la decisione del trigger, ma renderebbe le tracce
    // registrate diverse dal segnale su cui quella decisione e' stata presa.
    // In piu' sono calibrate sull'Output Mode: sui dati in Transparent Mode
    // lasciano il piedistallo corretto ma gonfiano l'RMS di circa 40 volte
    // (0.7 conteggi diventano ~28), e quell'RMS e' proprio il numero che si
    // guarda per scegliere la soglia.
    if (CAEN_DGTZ_DisableDRS4Correction(fHandle) != CAEN_DGTZ_Success)
        Log::OutWarning("Cannot disable DRS4 corrections: the recorded waveforms "
                        "will differ from the signal the comparator judged.");
    else
        Log::OutSummary("→ DRS4 corrections DISABLED and left off: the recorded "
                        "waveforms are the raw detector signal.");

    SetTransparentMode(true);
    bool ok = MeasureBaseline(fTransparentBaseline, fTransparentRMS, 10, "transparent");
    SetTransparentMode(false);

    if (!ok) {
        Log::OutError("Transparent Mode baseline measurement failed: "
                      "falling back to the absolute SelfTriggerThreshold values.");
        return;
    }

    const bool falling = (fTriggerPolarity == CAEN_DGTZ_TriggerOnFallingEdge);

    for (auto ch : fSelfTriggerChannels) {

        if (fTransparentBaseline.count(ch) == 0) {
            Log::OutWarning("No Transparent Mode baseline for ch" + std::to_string(ch) +
                            ": keeping the absolute SelfTriggerThreshold value (" +
                            std::to_string(fSelfTriggerThreshold[ch]) + ").");
            continue;
        }

        double base   = fTransparentBaseline[ch];
        double offset = static_cast<double>(fSelfTriggerOffset.count(ch)
                                            ? fSelfTriggerOffset[ch]
                                            : fSelfTriggerThresholdOffset);
        double thr    = falling ? base - offset : base + offset;

        if (thr < 0.0) {
            Log::OutWarning("ch" + std::to_string(ch) + ": threshold clipped to 0 (baseline = " +
                            std::to_string(base) + ", offset = " +
                            FmtOffset(offset) + ")");
            thr = 0.0;
        }
        if (thr > static_cast<double>(MAX_THRESHOLD_COUNTS)) {
            Log::OutWarning("ch" + std::to_string(ch) + ": threshold clipped to " +
                            std::to_string(MAX_THRESHOLD_COUNTS) + " (baseline = " +
                            std::to_string(base) + ")");
            thr = static_cast<double>(MAX_THRESHOLD_COUNTS);
        }

        fSelfTriggerThreshold[ch] = static_cast<uint32_t>(std::lround(thr));

        Log::OutSummary("   ch" + std::to_string(ch) +
                        ": transparent baseline = " + std::to_string(base) +
                        " (rms " + std::to_string(fTransparentRMS[ch]) + ")" +
                        " → threshold = " + std::to_string(fSelfTriggerThreshold[ch]));
    }
}

// =======================================================================
//  SELF-TRIGGER: scrittura soglie (0x1n80) e maschere di canale (0x1nA8)
// =======================================================================
void Digitizer::ApplySelfTriggerThresholds() {

    const uint32_t channelsPerGroup = 8;
    const uint32_t hwGroups = (fNChannels + channelsPerGroup - 1) / channelsPerGroup;

    // Maschera dei canali che partecipano all'OR, gruppo per gruppo
    std::vector<uint32_t> trgMask(hwGroups, 0);
    for (auto ch : fSelfTriggerChannels) {
        uint32_t group = ch / channelsPerGroup;
        if (group < hwGroups)
            trgMask[group] |= (1u << (ch % channelsPerGroup));
    }

    // Soglie: 0x1n80, bits[11:0] = soglia, bits[15:12] = indice canale nel gruppo
    for (auto ch : fSelfTriggerChannels)
        ApplyChannelThreshold(ch, fSelfTriggerThreshold.count(ch)
                                    ? fSelfTriggerThreshold[ch] : 0);

    // Maschere di trigger: 0x1nA8 (scritte su TUTTI i gruppi, cosi' i gruppi non
    // usati vengono esplicitamente azzerati)
    for (uint32_t group = 0; group < hwGroups; ++group) {
        uint32_t addr = GroupBaseAddress(group) + REG_GROUP_CH_TRG_MASK;
        CAEN_DGTZ_ErrorCode re = CAEN_DGTZ_WriteRegister(fHandle, addr, trgMask[group] & 0xFFu);
        if (re != CAEN_DGTZ_Success)
            Log::OutWarning("Cannot write channel trigger mask for group " +
                            std::to_string(group) + " (code = " + std::to_string(re) + ")");
        else
            Log::OutDebug("  → group " + std::to_string(group) + ": trigger mask = " +
                          IntToHex(trgMask[group]) + " at " + IntToHex(addr));
    }
}

// =======================================================================
//  SELF-TRIGGER: scrittura della soglia di un singolo canale
// =======================================================================
void Digitizer::ApplyChannelThreshold(uint32_t ch, uint32_t thr)
{
    const uint32_t channelsPerGroup = 8;
    uint32_t group    = ch / channelsPerGroup;
    uint32_t local_ch = ch % channelsPerGroup;
    if (group >= (fNChannels + channelsPerGroup - 1) / channelsPerGroup)
        return;

    uint32_t addr = GroupBaseAddress(group) + REG_GROUP_CH_THRESHOLD;
    uint32_t val  = ((local_ch & 0xFu) << 12) | (thr & MAX_THRESHOLD_COUNTS);

    CAEN_DGTZ_ErrorCode re = CAEN_DGTZ_WriteRegister(fHandle, addr, val);
    if (re != CAEN_DGTZ_Success)
        Log::OutWarning("Cannot write threshold for ch" + std::to_string(ch) +
                        " at " + IntToHex(addr) + " (code = " + std::to_string(re) + ")");
    else
        Log::OutDebug("  → ch" + std::to_string(ch) + ": wrote " + IntToHex(val) +
                      " at " + IntToHex(addr) + " (threshold = " + std::to_string(thr) + ")");

    fSelfTriggerThreshold[ch] = thr;
}

// =======================================================================
//  SELF-TRIGGER: disabilitazione completa
// =======================================================================
void Digitizer::ClearSelfTrigger(bool verbose) {

    const uint32_t channelsPerGroup = 8;
    const uint32_t hwGroups = (fNChannels + channelsPerGroup - 1) / channelsPerGroup;

    // Nessun canale partecipa all'OR
    for (uint32_t group = 0; group < hwGroups; ++group)
        CAEN_DGTZ_WriteRegister(fHandle,
                                GroupBaseAddress(group) + REG_GROUP_CH_TRG_MASK, 0x0);

    // Ripristina l'acquisizione automatica e toglie l'instradamento
    // "over-threshold" verso la motherboard
    CAEN_DGTZ_WriteRegister(fHandle, REG_BOARD_CONFIG_CLEAR, 1u << BIT_SELFTRG_NO_AUTOACQ);
    CAEN_DGTZ_WriteRegister(fHandle, REG_BOARD_CONFIG_CLEAR, 0xFu << MONITOR_SHIFT);

    // Nessun gruppo contribuisce al global trigger
    uint32_t gmask = 0;
    if (CAEN_DGTZ_ReadRegister(fHandle, REG_GLOBAL_TRIGGER_MASK, &gmask) == CAEN_DGTZ_Success)
        CAEN_DGTZ_WriteRegister(fHandle, REG_GLOBAL_TRIGGER_MASK, gmask & ~0xFu);

    if (verbose)
        Log::OutSummary("→ Self-trigger disabled on all groups.");
    else
        Log::OutDebug("Self-trigger registers cleared on all groups.");
}

// =======================================================================
//  SELF-TRIGGER: firmware check per la modalita' "global"
// =======================================================================
bool Digitizer::HasGlobalTriggerFirmware() const {
    // La modalita' Global Trigger e' disponibile da revisione 4.30_1.08
    // (ROC 4.30, AMC 1.08).
    auto parse = [](const char* rel, int& major, int& minor) {
        major = minor = -1;
        if (!rel) return false;
        return std::sscanf(rel, "%d.%d", &major, &minor) == 2;
    };

    int rocMaj, rocMin, amcMaj, amcMin;
    if (!parse(fBoardInfo.ROC_FirmwareRel, rocMaj, rocMin) ||
        !parse(fBoardInfo.AMC_FirmwareRel, amcMaj, amcMin)) {
        Log::OutWarning("Cannot parse firmware revisions: skipping the Global Trigger check.");
        return true;
    }

    bool rocOK = (rocMaj > 4) || (rocMaj == 4 && rocMin >= 30);
    bool amcOK = (amcMaj > 1) || (amcMaj == 1 && amcMin >= 8);

    return rocOK && amcOK;
}

// =======================================================================
//  SELF-TRIGGER: configurazione
// =======================================================================
void Digitizer::ConfigureSelfTrigger() {

    const uint32_t channelsPerGroup = 8;
    const uint32_t hwGroups = (fNChannels + channelsPerGroup - 1) / channelsPerGroup;

    if (fSelfTriggerChannels.empty()) {
        Log::OutError("SelfTrigger enabled but no channel participates in the trigger OR.");
        exit(1);
    }

    // La logica di self-trigger legge l'ADC mentre il DRS4 e' in Transparent Mode:
    // la latenza (~320 ns, ~420 ns in modo global) non e' compatibile con tutte
    // le frequenze di campionamento.
    if (fSamplingRateStr == "5GHz")
        Log::OutWarning("Self-trigger latency (~320 ns) is not compatible with 5 GHz sampling "
                        "(~200 ns acquisition window). Use 2.5 GHz, 1 GHz or 750 MHz.");
    else if (fSelfTriggerGlobal && fSamplingRateStr == "2.5GHz")
        Log::OutWarning("Global self-trigger latency (~420 ns) is not compatible with 2.5 GHz "
                        "sampling. Use 1 GHz or 750 MHz.");

    // 1-2. Soglie (eventualmente da baseline in Transparent Mode) e 3. maschere
    ComputeSelfTriggerThresholds();
    ApplySelfTriggerThresholds();

    if (!fSelfTriggerGlobal) {
        // Modo "paired": l'OR dei canali fa acquisire le coppie di gruppi
        // gr0/gr1 e gr2/gr3 in modo indipendente. L'acquisizione automatica
        // deve essere abilitata (0x8000[21] = 0).
        CAEN_DGTZ_WriteRegister(fHandle, REG_BOARD_CONFIG_CLEAR, 1u << BIT_SELFTRG_NO_AUTOACQ);
        CAEN_DGTZ_WriteRegister(fHandle, REG_BOARD_CONFIG_CLEAR, 0xFu << MONITOR_SHIFT);

        uint32_t gmask = 0;
        if (CAEN_DGTZ_ReadRegister(fHandle, REG_GLOBAL_TRIGGER_MASK, &gmask) == CAEN_DGTZ_Success)
            CAEN_DGTZ_WriteRegister(fHandle, REG_GLOBAL_TRIGGER_MASK, gmask & ~0xFu);

        Log::OutSummary("→ Self-trigger mode: PAIRED (OR on gr0/gr1 and gr2/gr3, independent).");
    }
    else {
        // Modo "global": i segnali over-threshold vanno alla motherboard, che ne
        // fa l'OR e genera un trigger globale per tutta la board.
        if (!HasGlobalTriggerFirmware())
            Log::OutWarning("Global self-trigger requires firmware >= 4.30_1.08 (found ROC " +
                            std::string(fBoardInfo.ROC_FirmwareRel) + ", AMC " +
                            std::string(fBoardInfo.AMC_FirmwareRel) +
                            "). The board may not trigger.");

        // 0x8000[21] = 1: disabilita l'acquisizione automatica del gruppo
        CAEN_DGTZ_WriteRegister(fHandle, REG_BOARD_CONFIG_SET, 1u << BIT_SELFTRG_NO_AUTOACQ);

        // 0x8000[31:28] = 0100: instrada gli over-threshold verso la motherboard
        CAEN_DGTZ_WriteRegister(fHandle, REG_BOARD_CONFIG_CLEAR, 0xFu << MONITOR_SHIFT);
        CAEN_DGTZ_WriteRegister(fHandle, REG_BOARD_CONFIG_SET,
                                MONITOR_SELFTRG_TO_MB << MONITOR_SHIFT);

        // 0x810C[3:0]: gruppi ammessi a generare il trigger globale.
        // Solo i gruppi che hanno almeno un canale di self-trigger.
        uint32_t groupMask = 0;
        for (auto ch : fSelfTriggerChannels) {
            uint32_t group = ch / channelsPerGroup;
            if (group < hwGroups)
                groupMask |= (1u << group);
        }

        uint32_t gmask = 0;
        CAEN_DGTZ_ErrorCode re =
            CAEN_DGTZ_ReadRegister(fHandle, REG_GLOBAL_TRIGGER_MASK, &gmask);
        if (re != CAEN_DGTZ_Success) {
            Log::OutWarning("Cannot read 0x810C: writing the group mask only.");
            gmask = 0;
        }
        gmask = (gmask & ~0xFu) | (groupMask & 0xFu);

        re = CAEN_DGTZ_WriteRegister(fHandle, REG_GLOBAL_TRIGGER_MASK, gmask);
        if (re != CAEN_DGTZ_Success)
            Log::OutWarning("Cannot write the global trigger mask (code = " +
                            std::to_string(re) + ")");

        Log::OutSummary("→ Self-trigger mode: GLOBAL (board-wide OR, group mask = " +
                        IntToHex(groupMask) + ").");
    }

    Log::OutSummary("→ Self-trigger threshold(s) applied:");
    for (auto ch : fSelfTriggerChannels)
        Log::OutSummary("   ch" + std::to_string(ch) + " → " +
                        std::to_string(fSelfTriggerThreshold[ch]) + " counts");
}

// =======================================================================
//  SOGLIE A RUN IN CORSO: rilettura del file di comando
// =======================================================================
void Digitizer::CheckLiveThresholds()
{
    // Il file contiene una riga per canale: "<canale> <offset>". L'offset e'
    // riferito alla baseline in Transparent Mode misurata all'avvio, che resta
    // valida: non serve fermare l'acquisizione ne' rimisurare.
    struct stat st;
    if (::stat(fLiveThresholdPath.c_str(), &st) != 0)
        return;                                  // nessun comando in attesa
    if (st.st_mtime == fLiveThresholdMtime)
        return;                                  // gia' applicato
    fLiveThresholdMtime = st.st_mtime;

    std::ifstream in(fLiveThresholdPath);
    if (!in) return;

    const bool falling = (fTriggerPolarity == CAEN_DGTZ_TriggerOnFallingEdge);
    bool changed   = false;   // una soglia e' cambiata davvero: nuova generazione
    bool republish = false;   // solo l'offset dichiarato e' cambiato
    std::string line;

    while (std::getline(in, line)) {
        if (line.empty() || line[0] == '#') continue;

        std::istringstream ss(line);
        long   ch  = -1;
        double off = -1.0;
        if (!(ss >> ch >> off)) {
            Log::OutWarning("Live threshold: cannot parse \"" + line + "\", ignored.");
            continue;
        }
        // Un resto sulla riga di solito e' un errore di battitura: meglio dirlo
        // che applicare qualcosa di diverso da quello che l'utente intendeva.
        std::string rest;
        if (ss >> rest) {
            Log::OutWarning("Live threshold: unexpected text \"" + rest +
                            "\" in \"" + line + "\", line ignored.");
            continue;
        }

        if (std::find(fSelfTriggerChannels.begin(), fSelfTriggerChannels.end(),
                      static_cast<uint32_t>(ch)) == fSelfTriggerChannels.end()) {
            Log::OutWarning("Live threshold: ch" + std::to_string(ch) +
                            " does not take part in the trigger, ignored.");
            continue;
        }
        if (off < 0.0 || off > static_cast<double>(MAX_THRESHOLD_COUNTS)) {
            Log::OutWarning("Live threshold: offset " + FmtOffset(off) +
                            " for ch" + std::to_string(ch) + " out of range, ignored.");
            continue;
        }

        uint32_t c = static_cast<uint32_t>(ch);
        if (fTransparentBaseline.count(c) == 0) {
            Log::OutWarning("Live threshold: no Transparent Mode baseline for ch" +
                            std::to_string(c) + ", ignored.");
            continue;
        }

        double base = fTransparentBaseline[c];
        double thr  = falling ? base - off : base + off;
        thr = std::max(0.0, std::min(thr, static_cast<double>(MAX_THRESHOLD_COUNTS)));

        uint32_t newthr = static_cast<uint32_t>(std::lround(thr));
        bool same_thr = fSelfTriggerThreshold.count(c) && fSelfTriggerThreshold[c] == newthr;

        // L'offset va registrato comunque, anche quando la soglia intera non
        // cambia: altrimenti lo stato pubblicato continuerebbe a dichiarare
        // quello vecchio e il monitor mostrerebbe un valore diverso da quello
        // che hai chiesto.
        if (fSelfTriggerOffset.count(c) == 0 || fSelfTriggerOffset[c] != off) {
            fSelfTriggerOffset[c] = off;
            republish = true;
        }

        if (same_thr) {
            Log::OutSummary("→ Live threshold: ch" + std::to_string(c) +
                            " offset = " + FmtOffset(off) +
                            " → threshold unchanged (" + std::to_string(newthr) +
                            "): rounds to the same 12-bit value as before.");
            continue;
        }

        ApplyChannelThreshold(c, newthr);
        changed = true;
        republish = true;

        Log::OutSummary("→ Live threshold: ch" + std::to_string(c) +
                        " offset = " + FmtOffset(off) +
                        " → threshold = " + std::to_string(newthr) +
                        " (event " + std::to_string(fH5Rows) + ")");
    }

    // La generazione avanza solo se una soglia e' cambiata davvero: e' cio' che
    // fa congelare al monitor la distribuzione precedente, e non avrebbe senso
    // farlo quando i dati acquisiti restano gli stessi.
    if (changed) {
        ++fThresholdGen;
        fThresholdGenRow = fH5Rows;
    }
    if (changed || republish)
        WriteStatusFile();
}

// =======================================================================
//  SOGLIE A RUN IN CORSO: stato pubblicato per il monitor
// =======================================================================
void Digitizer::WriteStatusFile()
{
    // Scritto su file temporaneo e rinominato: il monitor legge sempre un file
    // completo, mai uno a meta'.
    std::string tmp = fStatusPath + ".tmp";
    std::ofstream out(tmp);
    if (!out) return;

    out << "{\n";
    out << "  \"file\": \"" << std::filesystem::path(fOutputPath).filename().string() << "\",\n";
    out << "  \"generation\": " << fThresholdGen << ",\n";
    out << "  \"changed_at_event\": " << fThresholdGenRow << ",\n";
    out << "  \"polarity\": \""
        << (fTriggerPolarity == CAEN_DGTZ_TriggerOnFallingEdge ? "falling" : "rising")
        << "\",\n";
    out << "  \"channels\": [\n";
    for (size_t i = 0; i < fSelfTriggerChannels.size(); ++i) {
        uint32_t ch = fSelfTriggerChannels[i];
        out << "    {\"ch\": " << ch
            << ", \"offset\": " << (fSelfTriggerOffset.count(ch) ? fSelfTriggerOffset[ch] : 0.0)
            << ", \"threshold\": " << (fSelfTriggerThreshold.count(ch) ? fSelfTriggerThreshold[ch] : 0)
            << ", \"baseline\": " << (fTransparentBaseline.count(ch) ? fTransparentBaseline[ch] : 0.0)
            << "}" << (i + 1 < fSelfTriggerChannels.size() ? "," : "") << "\n";
    }
    out << "  ]\n}\n";
    out.close();

    std::error_code ec;
    std::filesystem::rename(tmp, fStatusPath, ec);
    if (ec)
        Log::OutWarning("Cannot publish the status file: " + ec.message());
}

// =======================================================================
//  DUMP dei registri di self-trigger (readback)
// =======================================================================
void Digitizer::DumpSelfTriggerRegisters() {

    const uint32_t channelsPerGroup = 8;
    const uint32_t hwGroups = (fNChannels + channelsPerGroup - 1) / channelsPerGroup;

    uint32_t val = 0;
    if (CAEN_DGTZ_ReadRegister(fHandle, REG_BOARD_CONFIG, &val) == CAEN_DGTZ_Success)
        Log::OutDebug("  0x8000 (Board Configuration) = " + IntToHex(val));
    if (CAEN_DGTZ_ReadRegister(fHandle, REG_GLOBAL_TRIGGER_MASK, &val) == CAEN_DGTZ_Success)
        Log::OutDebug("  0x810C (Global Trigger Mask) = " + IntToHex(val));

    for (uint32_t group = 0; group < hwGroups; ++group) {
        uint32_t addr = GroupBaseAddress(group) + REG_GROUP_CH_TRG_MASK;
        if (CAEN_DGTZ_ReadRegister(fHandle, addr, &val) == CAEN_DGTZ_Success)
            Log::OutDebug("  " + IntToHex(addr) + " (group " + std::to_string(group) +
                          " channel trigger mask) = " + IntToHex(val));
    }
}

// =======================================================================
//  CONFIGURE TRIGGER  (esterno + self-trigger)
// =======================================================================
void Digitizer::ConfigureTrigger() {

    Log::OutSummary("Configuring trigger sources...");

    // Parte da una board "quieta": nessun trigger esterno e nessun canale che
    // auto-triggera. Serve soprattutto perche' la misura della baseline in
    // Transparent Mode (modo "relative") deve vedere solo i trigger software.
    CAEN_DGTZ_SetExtTriggerInputMode(fHandle, CAEN_DGTZ_TRGMODE_DISABLED);
    ClearSelfTrigger(false);

    // --- Self-trigger dai canali di input ---
    fSelfTriggerMode = fSelfTrigger ? CAEN_DGTZ_TRGMODE_ACQ_ONLY : CAEN_DGTZ_TRGMODE_DISABLED;

    if (fSelfTrigger)
        ConfigureSelfTrigger();
    else
        Log::OutSummary("→ Self-trigger: DISABLED");

    // --- Trigger esterno su TRG-IN ---
    CAEN_DGTZ_TriggerMode_t extMode =
        fExternalTrigger ? fExternalTriggerMode : CAEN_DGTZ_TRGMODE_DISABLED;

    CAEN_DGTZ_ErrorCode re = CAEN_DGTZ_SetExtTriggerInputMode(fHandle, extMode);
    if (re != CAEN_DGTZ_Success)
        Log::OutWarning("SetExtTriggerInputMode failed (code = " + std::to_string(re) + ")");
    else
        Log::OutSummary(std::string("→ External trigger (TRG-IN): ") +
                        (fExternalTrigger ? "ENABLED" : "DISABLED"));

    if (!fSelfTrigger && !fExternalTrigger)
        Log::OutWarning("Neither external trigger nor self-trigger is enabled: "
                        "the board will only acquire on software triggers.");

    DumpSelfTriggerRegisters();

    // Il monitor legge da qui le soglie correnti. Il file di comando eventualmente
    // rimasto da una run precedente viene ignorato: se ne registra il timestamp
    // senza applicarlo, cosi' vale solo quello che scrivi da adesso.
    if (fSelfTrigger) {
        struct stat st;
        if (::stat(fLiveThresholdPath.c_str(), &st) == 0)
            fLiveThresholdMtime = st.st_mtime;
        WriteStatusFile();
        Log::OutSummary("→ Live thresholds: write \"<channel> <offset>\" lines into " +
                        fLiveThresholdPath + " to change them while running.");
    }

    Log::OutSummary("Trigger configuration complete.");
}

// =======================================================================
//  ACQUIRE EVENTS
// =======================================================================
void Digitizer::AcquireEvents() {
    if (!fBuffer || !fVoidEvent) {
        Log::OutError("AcquireEvents called before InitAcquisition().");
        return;
    }

    CAEN_DGTZ_ErrorCode re;

    const uint32_t channelsPerGroup = 8;
    uint32_t hwChannels = fNChannels;           // 32
    uint32_t hwGroups   = (hwChannels + channelsPerGroup - 1) / channelsPerGroup; // 4

    Log::OutDebug("AcquireEvents: hwChannels=" +
                  std::to_string(hwChannels) +
                  ", hwGroups=" + std::to_string(hwGroups) +
                  ", channelsPerGroup=" + std::to_string(channelsPerGroup));

    re = CAEN_DGTZ_SWStartAcquisition(fHandle);
    if (re != CAEN_DGTZ_Success) {
        Log::OutError("Start acquisition failed in AcquireEvents. Code: " + std::to_string(re));
        return;
    }
    fAcqRunning = true;

    std::string trgsrc;
    if (fExternalTrigger) trgsrc += "external";
    if (fSelfTrigger)     trgsrc += (trgsrc.empty() ? "" : " + ") + std::string("self");
    if (trgsrc.empty())   trgsrc = "software only";
    Log::OutSummary("→ Acquisition started (waiting for " + trgsrc + " triggers)");
    std::cout << std::endl;

    auto t_start = std::chrono::high_resolution_clock::now();

    uint32_t totalEvents = 0;
    const uint32_t maxEvents = fNEvents;
    const int maxRetries = 5000;
    int retry = 0;

    while (totalEvents < maxEvents && retry < maxRetries) {
        re = CAEN_DGTZ_ReadData(
            fHandle,
            CAEN_DGTZ_SLAVE_TERMINATED_READOUT_MBLT,
            fBuffer,
            &fBufferSize
        );
        if (re != CAEN_DGTZ_Success) {
            Log::OutError("ReadData failed in AcquireEvents. Code: " + std::to_string(re));
            break;
        }

        if (fBufferSize == 0) {
            if (fSelfTrigger) CheckLiveThresholds();
            std::this_thread::sleep_for(std::chrono::milliseconds(1000));
            ++retry;
            continue;
        }

        // Anche con trigger frequenti il file va controllato, ma non a ogni
        // ciclo: una stat al secondo e' abbastanza reattiva e non pesa.
        if (fSelfTrigger) {
            static auto lastcheck = std::chrono::steady_clock::now();
            auto now = std::chrono::steady_clock::now();
            if (std::chrono::duration_cast<std::chrono::milliseconds>(now - lastcheck).count() > 1000) {
                lastcheck = now;
                CheckLiveThresholds();
            }
        }

        uint32_t nEvents = 0;
        re = CAEN_DGTZ_GetNumEvents(fHandle, fBuffer, fBufferSize, &nEvents);
        if (re != CAEN_DGTZ_Success) {
            Log::OutError("GetNumEvents failed in AcquireEvents. Code: " + std::to_string(re));
            continue;
        }

        for (uint32_t j = 0; j < nEvents && totalEvents < maxEvents; ++j) {
            re = CAEN_DGTZ_GetEventInfo(
                fHandle, fBuffer, fBufferSize,
                j, &fEventInfo, &fEventPtr
            );
            if (re != CAEN_DGTZ_Success || !fEventPtr) {
                Log::OutError("GetEventInfo failed in AcquireEvents. Code: " + std::to_string(re));
                continue;
            }

            re = CAEN_DGTZ_DecodeEvent(fHandle, fEventPtr, &fVoidEvent);
            if (re != CAEN_DGTZ_Success || !fVoidEvent) {
                Log::OutError("DecodeEvent failed in AcquireEvents. Code: " + std::to_string(re));
                continue;
            }

            fEvent = reinterpret_cast<CAEN_DGTZ_X742_EVENT_t*>(fVoidEvent);

            std::vector<int16_t>  allSamplesCorr;
            std::vector<uint16_t> allSamplesRaw;

            for (uint32_t ch : fChannelList) {
                if (ch >= hwChannels)
                    continue;

                int group    = ch / channelsPerGroup;  // 8 ch per gruppo
                int local_ch = ch % channelsPerGroup;  // 0..7

                if (group < 0 || (uint32_t)group >= hwGroups)
                    continue;

                if (fEvent->GrPresent[group] == 0)
                    continue;

                uint32_t nsamples = fEvent->DataGroup[group].ChSize[local_ch];
                float* waveform   = fEvent->DataGroup[group].DataChannel[local_ch];

                if (nsamples < MIN_SAMPLES || nsamples > MAX_SAMPLES || waveform == nullptr) {
                    continue;
                }

                // Le forme d'onda si salvano GREZZE, senza sottrarre la
                // baseline. L'analisi la ricava evento per evento dalla
                // porzione pre-impulso, cosa piu' accurata di un unico valore
                // misurato all'avvio, e cosi' il dato originale resta intatto.
                uint32_t usable = (nsamples > fTailCut) ? (nsamples - fTailCut) : 0;

                for (uint32_t i = 0; i < usable; ++i) {
                    allSamplesCorr.push_back(static_cast<int16_t>(waveform[i]));

                    if (fSaveRaw)
                        allSamplesRaw.push_back(static_cast<uint16_t>(waveform[i]));
                }
            }

            std::cout << "\r→ Events decoded: "
                      << std::setw(6) << (totalEvents + 1)
                      << "/" << maxEvents << std::flush;

            if (fOutputFormat == kHDF5 && fH5File != nullptr && fH5Waveforms != nullptr) {

                // Il dataset ha larghezza fissa: un evento di dimensione diversa
                // (es. un gruppo assente, quindi canali saltati) non ci entra e
                // viene scartato con un avviso, invece di sfasare tutte le righe.
                if (allSamplesCorr.size() != fEventWidth) {
                    Log::OutWarning("Event " + std::to_string(totalEvents) +
                                    " has unexpected size (" +
                                    std::to_string(allSamplesCorr.size()) +
                                    " samples, expected " + std::to_string(fEventWidth) +
                                    "): skipped.");
                    ++fSkippedEvents;
                } else {
                    try {
                        AppendEvent(fH5Waveforms, allSamplesCorr.data(),
                                    PredType::NATIVE_INT16, fH5Rows);

                        if (fSaveRaw && fH5WaveformsRaw != nullptr &&
                            allSamplesRaw.size() == fEventWidth)
                            AppendEvent(fH5WaveformsRaw, allSamplesRaw.data(),
                                        PredType::NATIVE_UINT16, fH5Rows);

                        ++fH5Rows;

                        // In SWMR i lettori vedono i dati solo dopo una flush.
                        if (fLiveMonitoring && (fH5Rows % fFlushEvery) == 0) {
                            H5Dflush(fH5Waveforms->getId());
                            if (fH5WaveformsRaw != nullptr)
                                H5Dflush(fH5WaveformsRaw->getId());
                        }

                    } catch (const H5::Exception& e) {
                        Log::OutError("HDF5 write error: " + std::string(e.getDetailMsg()));
                    }
                }
            }

            ++totalEvents;
        }

        retry = 0;
    }

    auto t_end = std::chrono::high_resolution_clock::now();
    std::chrono::duration<double> elapsed = t_end - t_start;
    double elapsed_s = elapsed.count();
    double rate_kHz = (elapsed_s > 0.0)
        ? (totalEvents / elapsed_s / 1000.0)
        : 0.0;

    CAEN_DGTZ_SWStopAcquisition(fHandle);
    fAcqRunning = false;

    std::cout << std::endl << std::endl;
    Log::OutSummary("Acquisition complete.");
    Log::OutSummary("→ Total events recorded: " + std::to_string(totalEvents));
    Log::OutSummary("→ Acquisition time: " + std::to_string(elapsed_s) + " s");
    Log::OutSummary("→ Trigger rate: " + std::to_string(rate_kHz) + " kHz");
    Log::OutSummary("→ Events written to file: " + std::to_string(fH5Rows));
    if (fSkippedEvents > 0)
        Log::OutWarning("→ Events skipped (unexpected size): " +
                        std::to_string(fSkippedEvents));

    CloseOutputFile();
}

// =======================================================================
//  DIAGNOSTICA: acquisizione in Transparent Mode
// =======================================================================
void Digitizer::AcquireTransparent()
{
    if (!fBuffer || !fVoidEvent) {
        Log::OutError("AcquireTransparent called before InitAcquisition().");
        return;
    }

    const uint32_t channelsPerGroup = 8;
    const uint32_t hwGroups = (fNChannels + channelsPerGroup - 1) / channelsPerGroup;

    // VERIFICATO SPERIMENTALMENTE (23/09): questo dump registra correttamente
    // PIEDISTALLO e RUMORE del dominio in cui lavora il comparatore, ma NON
    // cattura gli impulsi. Con un impulso da pulser di 148 conteggi Output
    // Mode, largo 100 ns, che faceva scattare il self-trigger 300 volte con
    // soglia 20 conteggi sotto il piedistallo, la traccia registrata mostrava
    // un'escursione massima di 6 conteggi: l'impulso non entra nella finestra.
    // Per misurare l'ampiezza nel dominio della soglia si usa invece lo scan in
    // soglia, che adopera il comparatore stesso come strumento.
    Log::OutSummary("=====================================================");
    Log::OutSummary(" TRANSPARENT MODE DUMP");
    Log::OutSummary(" Misura piedistallo e rumore del dominio in cui");
    Log::OutSummary(" lavora il comparatore del self-trigger.");
    Log::OutSummary(" NON cattura gli impulsi: per quelli usare lo scan");
    Log::OutSummary(" in soglia (tools/noise_scan.py).");
    Log::OutSummary("=====================================================");

    // Le tabelle di correzione DRS4 sono tarate sull'Output Mode: applicarle
    // qui altererebbe proprio i valori che vogliamo osservare, che sono quelli
    // grezzi su cui lavora il comparatore.
    if (CAEN_DGTZ_DisableDRS4Correction(fHandle) == CAEN_DGTZ_Success)
        Log::OutSummary("→ DRS4 corrections DISABLED: raw ADC values.");
    else
        Log::OutWarning("→ Cannot disable DRS4 corrections: the values may be altered "
                        "with respect to what the comparator sees.");

    // Con "self" il board triggera sui propri ingressi mentre il DRS4 e' in
    // Transparent Mode: le tracce registrate sono quelle su cui lavora il
    // comparatore, con dentro l'impulso che ha fatto scattare il trigger. E'
    // l'unico modo di leggere l'ampiezza degli impulsi nel dominio della soglia.
    const bool selftrig = (fTransparentDumpTrigger == "self");
    if (selftrig && !fSelfTrigger) {
        Log::OutError("TransparentDumpTrigger = \"self\" requires SelfTrigger = true.");
        SetTransparentMode(false);
        return;
    }
    Log::OutSummary(std::string("→ Trigger: ") +
                    (selftrig ? "SELF (impulsi veri, come li vede il comparatore)"
                              : "software (solo piedistallo e rumore)"));

    SetTransparentMode(true);

    CAEN_DGTZ_ErrorCode re = CAEN_DGTZ_SWStartAcquisition(fHandle);
    if (re != CAEN_DGTZ_Success) {
        Log::OutError("Start acquisition failed in AcquireTransparent. Code: " +
                      std::to_string(re));
        SetTransparentMode(false);
        return;
    }
    fAcqRunning = true;

    uint32_t written = 0, skipped = 0;
    int idle = 0;
    const int maxidle = 600;            // ~60 s senza trigger e si rinuncia

    while (written < fTransparentDumpEvents) {

        if (!selftrig && CAEN_DGTZ_SendSWtrigger(fHandle) != CAEN_DGTZ_Success) break;

        fBufferSize = 0;
        if (CAEN_DGTZ_ReadData(fHandle, CAEN_DGTZ_SLAVE_TERMINATED_READOUT_MBLT,
                               fBuffer, &fBufferSize) != CAEN_DGTZ_Success)
            break;
        if (fBufferSize == 0) {
            if (selftrig && ++idle > maxidle) {
                Log::OutWarning("No self-trigger for 60 s: giving up.");
                break;
            }
            if (selftrig) std::this_thread::sleep_for(std::chrono::milliseconds(100));
            continue;
        }
        idle = 0;

        uint32_t nEvents = 0;
        if (CAEN_DGTZ_GetNumEvents(fHandle, fBuffer, fBufferSize, &nEvents)
                != CAEN_DGTZ_Success)
            continue;

        for (uint32_t j = 0; j < nEvents; ++j) {
            if (CAEN_DGTZ_GetEventInfo(fHandle, fBuffer, fBufferSize, j,
                                       &fEventInfo, &fEventPtr) != CAEN_DGTZ_Success
                || !fEventPtr)
                continue;
            if (CAEN_DGTZ_DecodeEvent(fHandle, fEventPtr, &fVoidEvent)
                    != CAEN_DGTZ_Success || !fVoidEvent)
                continue;

            fEvent = reinterpret_cast<CAEN_DGTZ_X742_EVENT_t*>(fVoidEvent);

            std::vector<int16_t> samples;
            samples.reserve(fEventWidth);

            for (uint32_t ch : fChannelList) {
                uint32_t group    = ch / channelsPerGroup;
                uint32_t local_ch = ch % channelsPerGroup;
                if (group >= hwGroups || fEvent->GrPresent[group] == 0) continue;

                uint32_t ns     = fEvent->DataGroup[group].ChSize[local_ch];
                float*   wave   = fEvent->DataGroup[group].DataChannel[local_ch];
                if (ns < MIN_SAMPLES || ns > MAX_SAMPLES || wave == nullptr) continue;

                uint32_t usable = (ns > fTailCut) ? (ns - fTailCut) : 0;
                for (uint32_t k = 0; k < usable; ++k)
                    samples.push_back(static_cast<int16_t>(wave[k]));
            }

            if (samples.size() != fEventWidth) { ++skipped; continue; }

            try {
                AppendEvent(fH5Waveforms, samples.data(), PredType::NATIVE_INT16, fH5Rows);
                ++fH5Rows;
                ++written;
                if (fLiveMonitoring && (fH5Rows % fFlushEvery) == 0)
                    H5Dflush(fH5Waveforms->getId());
            } catch (const H5::Exception& e) {
                Log::OutError("HDF5 write error: " + std::string(e.getDetailMsg()));
            }
        }

        std::cout << "\r→ Transparent events: " << std::setw(6) << written
                  << "/" << fTransparentDumpEvents << std::flush;

        // Trigger software ravvicinati campionerebbero sempre la stessa fase
        if (!selftrig)
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }

    std::cout << std::endl;

    CAEN_DGTZ_SWStopAcquisition(fHandle);
    fAcqRunning = false;
    SetTransparentMode(false);

    Log::OutSummary("→ Transparent Mode events written: " + std::to_string(written));
    if (skipped)
        Log::OutWarning("→ Events skipped (unexpected size): " + std::to_string(skipped));
    Log::OutSummary("→ File: " + fOutputPath);

    CloseOutputFile();
}

// =======================================================================
//  GET TIME (ms)
// =======================================================================
long Digitizer::GetTime() {
    struct timeval t1;
    gettimeofday(&t1, nullptr);
    return t1.tv_sec * 1000 + t1.tv_usec / 1000;
}

// =======================================================================
//  CHECK ACCEPTED NOISE EVENTS
// =======================================================================
bool Digitizer::CheckAccepted(std::map<uint32_t,uint32_t>& nAccepted) {
    for (auto& c : fChannelList)
        if (nAccepted[c] < fNNoiseEvents)
            return false;
    return true;
}

// =======================================================================
//  HDF5: append di un evento al dataset estendibile
// =======================================================================
void Digitizer::AppendEvent(H5::DataSet* ds,
                            const void* data,
                            const H5::DataType& type,
                            hsize_t row)
{
    hsize_t newsize[2] = {row + 1, fEventWidth};
    ds->extend(newsize);

    H5::DataSpace fspace = ds->getSpace();
    hsize_t offset[2] = {row, 0};
    hsize_t count[2]  = {1, fEventWidth};
    fspace.selectHyperslab(H5S_SELECT_SET, count, offset);

    H5::DataSpace mspace(2, count);
    ds->write(data, type, mspace, fspace);
}

// =======================================================================
//  PREPARE OUTPUT FILE  (HDF5)
// =======================================================================
void Digitizer::PrepareOutput() {
    if (fOutputFormat != kHDF5)
        return;

    if (!std::filesystem::exists(fOutputDir)) {
        Log::OutError("Output directory does not exist: " + fOutputDir);
        exit(1);
    }

    std::string base = fOutputFileName;

    int maxRun = -1;

    try {
        for (const auto& entry : std::filesystem::directory_iterator(fOutputDir)) {
            if (!entry.is_regular_file())
                continue;

            std::string name = entry.path().filename().string();

            if (name.rfind(base + "_", 0) != 0)
                continue;

            if (name.find(".h5") == std::string::npos)
                continue;

            // Il numero di run sta subito dopo il prefisso "base_", non dopo il
            // primo underscore: se OutputFile contiene un underscore (es.
            // "WC_proto") cercare il primo lo fa leggere dentro il nome stesso,
            // scartare tutti i file e ripartire sempre da 0000, troncando la run
            // precedente.
            size_t pos = base.size();
            if (name.size() < pos + 5)
                continue;

            std::string runStr = name.substr(pos + 1, 4);

            if (!std::all_of(runStr.begin(), runStr.end(), ::isdigit))
                continue;

            int r = std::stoi(runStr);
            if (r > maxRun)
                maxRun = r;
        }
    } catch (...) {
        Log::OutWarning("→ Unable to scan existing runs, defaulting to RunNumber = 0.");
    }

    fRunNumber = maxRun + 1;

    if (fRunNumber < 0 || fRunNumber > 9999) {
        Log::OutError("Computed run number out of range: " + std::to_string(fRunNumber));
        exit(1);
    }

    std::string rateTag = "_unkRate";
    if (fSamplingRateStr == "5GHz")         rateTag = "_5Gs";
    else if (fSamplingRateStr == "2.5GHz")  rateTag = "_2.5Gs";
    else if (fSamplingRateStr == "1GHz")    rateTag = "_1Gs";
    else {
        std::string s = fSamplingRateStr;
        s.erase(std::remove_if(s.begin(), s.end(), ::isspace), s.end());
        if (s.find("5")   != std::string::npos)   rateTag = "_5Gs";
        else if (s.find("2.5") != std::string::npos) rateTag = "_2.5Gs";
        else if (s.find("1")   != std::string::npos)   rateTag = "_1Gs";
    }

    std::ostringstream fname;
    fname << fOutputDir << "/"
          << base << "_"
          << std::setw(4) << std::setfill('0') << fRunNumber
          << rateTag
          << "_" << fPostTriggerSize << "PT"
          << ".h5";

    fOutputPath = fname.str();

    // Rete di sicurezza: il file viene aperto con H5F_ACC_TRUNC, quindi se il
    // nome collidesse con una run esistente i dati sarebbero persi senza
    // preavviso. Piuttosto si cerca il primo numero libero.
    while (std::filesystem::exists(fOutputPath) ||
           std::filesystem::exists(fOutputPath + ".gz")) {
        Log::OutWarning("→ Run file already exists: " + fOutputPath +
                        " — trying the next run number.");
        if (++fRunNumber > 9999) {
            Log::OutError("No free run number available in " + fOutputDir);
            exit(1);
        }
        std::ostringstream retry;
        retry << fOutputDir << "/" << base << "_"
              << std::setw(4) << std::setfill('0') << fRunNumber
              << rateTag << "_" << fPostTriggerSize << "PT" << ".h5";
        fOutputPath = retry.str();
    }

    Log::OutSummary("→ HDF5 output path selected: " + fOutputPath);

    try {
        // SWMR richiede che il file sia scritto con il formato piu' recente.
        // NOTA: i file risultanti non sono leggibili da libhdf5 < 1.10.
        H5::FileAccPropList fapl;
        fapl.setLibverBounds(H5F_LIBVER_LATEST, H5F_LIBVER_LATEST);

        fH5File  = new H5::H5File(fOutputPath, H5F_ACC_TRUNC,
                                  H5::FileCreatPropList::DEFAULT, fapl);
        fH5Group = new H5::Group(fH5File->createGroup("/events"));
        H5::Group header = fH5File->createGroup("/config");

        // Larghezza di una riga: i canali sono concatenati, come nel formato v1.
        // Va fissata ora perche' il dataset viene creato prima di leggere il
        // primo evento; gli eventi che non la rispettano vengono scartati con
        // un avviso invece di corrompere il dataset.
        const uint32_t samplesPerChannel =
            (fRecordLength > fTailCut) ? (fRecordLength - fTailCut) : 0;
        fEventWidth = static_cast<hsize_t>(fChannelList.size()) * samplesPerChannel;

        if (fEventWidth == 0) {
            Log::OutError("Event width is zero (RecordLength = " +
                          std::to_string(fRecordLength) + ", TailCut = " +
                          std::to_string(fTailCut) + "). Abort.");
            exit(1);
        }

        auto MakeWaveformDataset = [&](const std::string& path,
                                       const H5::DataType& type) {
            hsize_t dims[2]    = {0, fEventWidth};
            hsize_t maxdims[2] = {H5S_UNLIMITED, fEventWidth};
            H5::DataSpace space(2, dims, maxdims);

            H5::DSetCreatPropList dcpl;
            hsize_t chunk[2] = {H5_CHUNK_EVENTS, fEventWidth};
            dcpl.setChunk(2, chunk);

            return new H5::DataSet(fH5File->createDataSet(path, type, space, dcpl));
        };

        fH5Waveforms = MakeWaveformDataset("/events/waveforms",
                                           H5::PredType::NATIVE_INT16);
        if (fSaveRaw) {
            fH5File->createGroup("/events_raw");
            fH5WaveformsRaw = MakeWaveformDataset("/events_raw/waveforms",
                                                  H5::PredType::NATIVE_UINT16);
        }
        fH5Rows = 0;

        header.createAttribute("RunNumber", H5::PredType::NATIVE_INT,
                               H5::DataSpace()).write(H5::PredType::NATIVE_INT, &fRunNumber);

        header.createAttribute("RecordLength", H5::PredType::NATIVE_UINT,
                               H5::DataSpace()).write(H5::PredType::NATIVE_UINT, &fRecordLength);

        header.createAttribute("PostTriggerSize", H5::PredType::NATIVE_UINT,
                               H5::DataSpace()).write(H5::PredType::NATIVE_UINT, &fPostTriggerSize);

        int tmflag = fTransparentDump ? 1 : 0;
        header.createAttribute("TransparentMode", H5::PredType::NATIVE_INT,
                               H5::DataSpace()).write(H5::PredType::NATIVE_INT, &tmflag);

        int fmtver = OUTPUT_FORMAT_VERSION;
        header.createAttribute("FormatVersion", H5::PredType::NATIVE_INT,
                               H5::DataSpace()).write(H5::PredType::NATIVE_INT, &fmtver);

        header.createAttribute("TailCut", H5::PredType::NATIVE_UINT,
                               H5::DataSpace()).write(H5::PredType::NATIVE_UINT, &fTailCut);

        uint32_t spc = samplesPerChannel;
        header.createAttribute("SamplesPerChannel", H5::PredType::NATIVE_UINT,
                               H5::DataSpace()).write(H5::PredType::NATIVE_UINT, &spc);

        double sampling_ns = fSamplingTime;
        header.createAttribute("SamplingTime", H5::PredType::NATIVE_DOUBLE,
                               H5::DataSpace()).write(H5::PredType::NATIVE_DOUBLE, &sampling_ns);

        {
            H5::StrType stype(H5::PredType::C_S1, H5T_VARIABLE);
            header.createAttribute("SamplingRate", stype, H5::DataSpace())
                  .write(stype, fSamplingRateStr);
        }

        {
            H5::StrType stype(H5::PredType::C_S1, H5T_VARIABLE);
            header.createAttribute("OutputFormat", stype, H5::DataSpace())
                  .write(stype, std::string("HDF5"));
        }

        std::string trig_mode;
        if (fExternalTrigger) trig_mode += "External";
        if (fSelfTrigger)     trig_mode += (trig_mode.empty() ? "" : "+") + std::string("Self");
        if (trig_mode.empty()) trig_mode = "Disabled";
        {
            H5::StrType stype(H5::PredType::C_S1, H5T_VARIABLE);
            header.createAttribute("TriggerMode", stype, H5::DataSpace())
                  .write(stype, trig_mode);
        }

        if (fSelfTrigger && !fSelfTriggerChannels.empty()) {
            {
                H5::StrType stype(H5::PredType::C_S1, H5T_VARIABLE);
                header.createAttribute("SelfTriggerMode", stype, H5::DataSpace())
                      .write(stype, fSelfTriggerModeStr);
            }

            hsize_t dim = fSelfTriggerChannels.size();
            H5::DataSpace dspace(1, &dim);
            header.createAttribute("SelfTriggerChannels",
                                   H5::PredType::NATIVE_UINT, dspace)
                  .write(H5::PredType::NATIVE_UINT, fSelfTriggerChannels.data());

            std::vector<uint32_t> thr;
            for (auto ch : fSelfTriggerChannels)
                thr.push_back(fSelfTriggerThreshold.count(ch) ? fSelfTriggerThreshold[ch] : 0);
            header.createAttribute("SelfTriggerThreshold",
                                   H5::PredType::NATIVE_UINT, dspace)
                  .write(H5::PredType::NATIVE_UINT, thr.data());
        }

        if (!fChannelList.empty()) {
            hsize_t dim = fChannelList.size();
            H5::DataSpace dspace(1, &dim);
            H5::Attribute chattr =
                header.createAttribute("ChannelList",
                                       H5::PredType::NATIVE_UINT,
                                       dspace);
            chattr.write(H5::PredType::NATIVE_UINT, fChannelList.data());
        }

        // ConfigureTrigger() gira prima di PrepareOutput(), quindi il primo
        // stato pubblicato aveva il nome del file vuoto e il monitor lo
        // scartava credendolo di un'altra run. Ora che fOutputPath esiste, si
        // ripubblica.
        if (fSelfTrigger)
            WriteStatusFile();

        // Da qui in poi nessun oggetto nuovo puo' essere creato nel file: SWMR
        // lo vieta. Tutti i gruppi, i dataset e gli attributi sono gia' stati
        // creati sopra.
        if (fLiveMonitoring) {
            if (H5Fstart_swmr_write(fH5File->getId()) < 0) {
                Log::OutWarning("Cannot enable SWMR: the file will only be readable "
                                "after the run ends.");
                fLiveMonitoring = false;
            } else {
                Log::OutSummary("→ SWMR enabled: the file can be read while the run "
                                "is ongoing (flush every " +
                                std::to_string(fFlushEvery) + " events).");
            }
        }

    } catch (const H5::Exception& e) {
        Log::OutError("HDF5 file creation failed: " + std::string(e.getDetailMsg()));
        exit(1);
    }
}

// =======================================================================
//  CLOSE OUTPUT FILE + GZIP
// =======================================================================
void Digitizer::CloseOutputFile() {
    if (fOutputFormat != kHDF5 || fH5File == nullptr)
        return;

    try {
        if (fH5Waveforms != nullptr) {
            fH5Waveforms->close();
            delete fH5Waveforms;
            fH5Waveforms = nullptr;
        }
        if (fH5WaveformsRaw != nullptr) {
            fH5WaveformsRaw->close();
            delete fH5WaveformsRaw;
            fH5WaveformsRaw = nullptr;
        }
        if (fH5Group != nullptr) {
            fH5Group->close();
        }
        fH5File->close();

        delete fH5Group;
        delete fH5File;
        fH5Group = nullptr;
        fH5File  = nullptr;

        std::string gzFile = fOutputPath + ".gz";
        Log::OutSummary("→ Compressing HDF5 file: " + fOutputPath);

        std::string cmd = "gzip -f \"" + fOutputPath + "\"";
        int ret = system(cmd.c_str());

        if (ret == 0)
            Log::OutSummary("→ HDF5 file compressed: " + gzFile);
        else
            Log::OutError("→ Compression failed. Code = " + std::to_string(ret));

        Log::OutSummary("→ HDF5 file closed and cleaned up.");
    }
    catch (const H5::Exception& e) {
        Log::OutError("→ HDF5 file close failed: " + std::string(e.getDetailMsg()));
    }
}

// =======================================================================
//  HEX UTILITY
// =======================================================================
std::string Digitizer::IntToHex(uint32_t val) {
    std::stringstream stream;
    stream << "0x" << std::hex << std::uppercase << val;
    return stream.str();
}
