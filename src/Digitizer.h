#ifndef DIGITIZER_HH
#define DIGITIZER_HH

#include <iostream>
#include <vector>
#include <map>
#include <string>
#include <chrono>
#include <fstream>
#include <sstream>
#include <H5Cpp.h>

#include "CAENVMElib.h"
#include "CAENDigitizer.h"
#include "CAENDigitizerType.h"

#include "Config.h"

class Digitizer {
public:
    enum OutputFormat { kASCII, kHDF5 };

    Digitizer();
    ~Digitizer();

    int GetHandle() const { return fHandle; }

    void InitAcquisition();
    void SetTriggerThreshold(double offset = 0.1);
    void ConfigureTrigger();
    void PrepareOutput();
    void AcquireEvents();
    void CloseOutputFile();
    void Reset();
    void Close();
    void GetVMElibVersion();
    void SelectBoard();
    void Configure();

private:

    // ===== === SAME ORDER AS CONSTRUCTOR === =====

    // ---- CONFIG ----
    Config& fConfig;
    bool fIsRunning;

    // Track if SW acquisition was started inside SetTriggerThreshold()
    bool fAcqRunning;   // moved to correct order

    // ---- CONNECTION ----
    CAEN_DGTZ_ConnectionType fConnectionType;
    std::string fIPAddress;
    int fConetNode;
    uint32_t fVMEBaseAddress;
    int fHandle;
    CAEN_DGTZ_BoardInfo_t fBoardInfo;

    // ---- ACQUISITION SETTINGS ----
    uint32_t fRecordLength;
    uint32_t fNChannels;
    uint32_t fChannelMask;
    uint32_t fNActiveChannels;
    uint32_t fPostTriggerSize;
	uint32_t fTailCut;
	CAEN_DGTZ_AcqMode_t fAcquisitionMode;
    uint32_t fNTransferedEvents;
    uint32_t fGroupMask;

    bool fSelfTrigger;
    bool fSaveRaw;
    bool fExternalTrigger;

    CAEN_DGTZ_TriggerMode_t  fSelfTriggerMode;
    CAEN_DGTZ_TriggerMode_t  fExternalTriggerMode;

    CAEN_DGTZ_PulsePolarity_t   fPulsePolarity;
    CAEN_DGTZ_TriggerPolarity_t fTriggerPolarity;

    std::vector<uint32_t> fChannelList;
    uint32_t fBoardChannels;   // declared here to match where ctor initializes it

    double fNRMSThreshold;
    double fIntegralThreshold;

    // ---- BASELINE ----
    std::map<uint32_t,double> fBaselineMean;

    // ---- BUFFERS ----
    char* fBuffer;
    uint32_t fBufferSize;
    CAEN_DGTZ_EventInfo_t fEventInfo;
    void* fVoidEvent;
    CAEN_DGTZ_X742_EVENT_t* fEvent;
    char* fEventPtr;

    // ---- RUN-TIME ----
    double fWaitTimeS;
    double fSamplingTime;
    std::string fSamplingRateStr;

    std::chrono::seconds fACQT;
    double fDeadT;

    uint32_t fNNoiseEvents;
    uint32_t fNEvents;
    uint32_t fDuration;

    // ---- OUTPUT SETTINGS ----
    OutputFormat fOutputFormat;

    std::string fOutputDir;
    std::string fOutputFileName;
    std::string fOutputPath;

    int fRunNumber;

    std::ofstream fASCIIFile;

    std::map<uint32_t, std::vector<uint16_t>*> fOutEvent;

    // ---- TIMESTAMPS (must match ctor order) ----
    unsigned long int fTimestamp_s;
    unsigned long int fTimestamp_ns;
    unsigned long int fTriggerTime;

    // ---- SELF-TRIGGER (V1742 channel auto-trigger) ----
    // Modalita' self-trigger: "paired" = OR sui gruppi accoppiati (gr0/gr1, gr2/gr3),
    // "global" = OR su tutta la board (richiede firmware >= 4.30_1.08).
    std::string fSelfTriggerModeStr;
    bool fSelfTriggerGlobal;
    // "absolute" = soglia in conteggi ADC assoluti, "relative" = offset rispetto
    // alla baseline misurata in Transparent Mode.
    std::string fSelfTriggerThresholdMode;
    bool fSelfTriggerRelative;
    std::vector<uint32_t> fSelfTriggerChannels;
    std::map<uint32_t,uint32_t> fSelfTriggerThreshold;   // per canale, 12 bit (0..4095)
    double fSelfTriggerThresholdOffset;                  // conteggi ADC, modo "relative"
    std::map<uint32_t,double> fSelfTriggerOffset;        // stesso, ma per canale
    std::map<uint32_t,double> fTransparentBaseline;      // baseline in Transparent Mode
    std::map<uint32_t,double> fTransparentRMS;

    // Lette dal TOML nel costruttore (l'ordine deve combaciare con la lista
    // di inizializzazione); i membri operativi sono piu' sotto, in ---- HDF5 ----
    bool     fLiveMonitoringCfg;
    uint32_t fFlushEveryCfg;
    std::string fLiveThresholdFileCfg;   // vuoto = <OutputDir>/live-threshold.txt

    // ---- SOGLIE MODIFICABILI A RUN IN CORSO ----
    std::string fLiveThresholdPath;      // file di comando, riletto se cambia
    std::string fStatusPath;             // stato pubblicato per il monitor
    long        fLiveThresholdMtime;     // per accorgersi delle modifiche
    uint64_t    fThresholdGen;           // incrementa a ogni cambio di soglia
    uint64_t    fThresholdGenRow;        // evento in cui e' avvenuto il cambio

    // ---- UTILITIES ----
    bool CheckAccepted(std::map<uint32_t,uint32_t>& nAccepted);
    static long GetTime();

    // ---- HDF5 HELPERS ----
    void AppendEvent(H5::DataSet* ds,
                     const void* data,
                     const H5::DataType& type,
                     hsize_t row);

    // ---- SELF-TRIGGER HELPERS ----
    void ConfigureSelfTrigger();
    void ClearSelfTrigger(bool verbose = true);
    void ComputeSelfTriggerThresholds();
    void ApplySelfTriggerThresholds();
    void ApplyChannelThreshold(uint32_t ch, uint32_t thr);
    void CheckLiveThresholds();
    void WriteStatusFile();
    void SetTransparentMode(bool enable);
    void DumpSelfTriggerRegisters();
    bool HasGlobalTriggerFirmware() const;
    bool MeasureBaseline(std::map<uint32_t,double>& mean,
                         std::map<uint32_t,double>& rms,
                         uint32_t ntriggers,
                         const std::string& tag);

    static constexpr uint32_t GroupBaseAddress(uint32_t group) { return 0x1000u + 0x100u * group; }

    // ---- HDF5 ----
    H5::H5File*  fH5File;
    H5::Group*   fH5Group;

    // Formato v2: un unico dataset estendibile invece di un dataset per evento.
    // E' il requisito per SWMR, che permette di leggere il file mentre la run
    // e' ancora in corso (SWMR vieta di creare oggetti nuovi a file aperto).
    H5::DataSet* fH5Waveforms;
    H5::DataSet* fH5WaveformsRaw;
    hsize_t      fH5Rows;         // eventi gia' scritti nel dataset
    hsize_t      fEventWidth;     // campioni per evento = n_canali * (RecordLength - TailCut)
    uint32_t     fSkippedEvents;  // eventi scartati perche' di dimensione inattesa
    bool         fLiveMonitoring; // attiva SWMR + flush periodica
    uint32_t     fFlushEvery;     // ogni quanti eventi fare flush per i lettori

    // ---- CONSTANTS ----
    // ---- V1742 REGISTERS (cfr. UM5698 - 742 Registers Description) ----
    static constexpr uint32_t REG_GROUP_CH_THRESHOLD = 0x0080;  // 0x1n80, offset nel gruppo
    static constexpr uint32_t REG_GROUP_CH_TRG_MASK  = 0x00A8;  // 0x1nA8, offset nel gruppo
    static constexpr uint32_t REG_BOARD_CONFIG       = 0x8000;
    static constexpr uint32_t REG_BOARD_CONFIG_SET   = 0x8004;  // bit set
    static constexpr uint32_t REG_BOARD_CONFIG_CLEAR = 0x8008;  // bit clear
    static constexpr uint32_t REG_GLOBAL_TRIGGER_MASK= 0x810C;
    static constexpr uint32_t BIT_TRANSPARENT_MODE   = 13;      // 0x8000[13]
    static constexpr uint32_t BIT_SELFTRG_NO_AUTOACQ = 21;      // 0x8000[21]
    static constexpr uint32_t MONITOR_SHIFT          = 28;      // 0x8000[31:28]
    static constexpr uint32_t MONITOR_SELFTRG_TO_MB  = 0x4;     // 0100 = over-threshold -> motherboard
    static constexpr uint32_t MAX_THRESHOLD_COUNTS   = 0x0FFF;  // soglia a 12 bit

    // Versione del formato di output, scritta in /config per i lettori:
    //   1 = un dataset per evento (/events/eventN)   [storico]
    //   2 = un dataset estendibile (/events/waveforms) + SWMR
    static constexpr int      OUTPUT_FORMAT_VERSION = 2;
    static constexpr hsize_t  H5_CHUNK_EVENTS = 8;   // eventi per chunk HDF5

    static constexpr uint32_t MAX_CHANNELS = 64;
    static constexpr uint32_t MAX_SAMPLES  = 100000;
    static constexpr uint32_t MIN_SAMPLES  = 10;

    std::string IntToHex(uint32_t val);
};

#endif