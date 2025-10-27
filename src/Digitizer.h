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

#include "TFile.h"
#include "TTree.h"
#include "TParameter.h"

#include "Config.h"

class Digitizer {
public:
    enum OutputFormat { kROOT, kASCII, kHDF5 };

    Digitizer();
    ~Digitizer();
  int GetHandle() const { return fHandle; }

  void InitAcquisition();
  void SetTriggerThreshold(double offset = 0.1);
  void PrepareOutput();
  void AcquireEvents();
  void CloseOutputFile();
  void CountExternalTriggers(int seconds);
  void Reset();
  void Close();
  void GetVMElibVersion();
  void SafeCleanup();
  void SelectBoard();
  void Configure();
  
private:
  Config& fConfig;
  bool fIsRunning;
  
  std::string IntToHex(uint32_t val);
  
  static constexpr uint32_t MAX_CHANNELS = 64;
  static constexpr uint32_t MAX_SAMPLES = 100000;
  static constexpr uint32_t MIN_SAMPLES = 10;
  CAEN_DGTZ_ConnectionType fConnectionType;
  std::string fIPAddress;
  int fConetNode;
  uint32_t fVMEBaseAddress;
  int fHandle;
  CAEN_DGTZ_BoardInfo_t fBoardInfo;

  uint32_t fRecordLength;
  uint32_t fNChannels;
  uint32_t fChannelMask;
    std::vector<uint32_t> fChannelList;
  uint32_t fNActiveChannels;
    uint32_t fPostTriggerSize;
  CAEN_DGTZ_AcqMode_t fAcquisitionMode;
    uint32_t fNTransferedEvents;
    uint32_t fGroupMask;

    bool fSelfTrigger;
    bool fSaveRaw; ///< it true save also the raw signal, non baseline corrected

    bool fExternalTrigger;
    CAEN_DGTZ_TriggerMode_t fSelfTriggerMode;
    CAEN_DGTZ_TriggerMode_t fExternalTriggerMode;
    CAEN_DGTZ_PulsePolarity_t fPulsePolarity;
    CAEN_DGTZ_TriggerPolarity_t fTriggerPolarity;

    std::map<uint32_t, uint32_t> fTriggerThreshold;
    std::map<uint32_t, double> fBaselineMean;
    std::map<uint32_t, double> fBaselineVar;
    std::map<uint32_t, double> fBaselineRMS;
    double fNRMSThreshold;
    double fIntegralThreshold;

    char* fBuffer;
    uint32_t fBufferSize;
    CAEN_DGTZ_EventInfo_t fEventInfo;
    void* fVoidEvent;
    CAEN_DGTZ_X742_EVENT_t* fEvent;
    char* fEventPtr;

    double fWaitTimeS;
    double fSamplingTime;
    std::chrono::seconds fACQT;
    double fDeadT;
    uint32_t fNNoiseEvents;
    uint32_t fNEvents;
    uint32_t fDuration;

    OutputFormat fOutputFormat;
    std::string fOutputDir;
    std::string fOutputFileName;
  std::string fOutputPath; 

    int fRunNumber;
    TFile* fROOTFile;
    TTree* fChannelTree;
    TTree* fEventTree;
    std::ofstream fASCIIFile;
    std::map<uint32_t, std::vector<uint16_t>*> fOutEvent;

    unsigned long int fTimestamp_s;
    unsigned long int fTimestamp_ns;
    unsigned long int fTriggerTime;

    bool CheckAccepted(std::map<uint32_t, uint32_t>& nAccepted);
    static long GetTime();

    // HDF5
    H5::H5File* fH5File = nullptr;
  //H5::Group fH5Group;
  H5::Group* fH5Group = nullptr;
};

#endif
