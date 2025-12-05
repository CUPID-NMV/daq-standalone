// Digitizer.cpp aggiornato per V1742
// 11/07.....Digitizer riscritto completamente...
//
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

#include "Digitizer.h"
#include "Log.h"
#include "TParameter.h"
#include <CAENDigitizer.h>
#include <CAENDigitizerType.h>
#include "HDF5Writer.hpp"
#include <H5Cpp.h>
 
using namespace H5;

Digitizer::Digitizer() :
  fConfig(Config::GetInstance()),
  fIsRunning(true),

  fConnectionType(CAEN_DGTZ_ETH_V4718),
  fIPAddress(fConfig.GetEntry<std::string>("digitizer", "IPAddress", "192.168.99.105")),
  fConetNode(fConfig.GetEntry<int>("digitizer", "ConetNode", 0)),
  fVMEBaseAddress(0x32100000),
  fHandle(0),
    
  fRecordLength(fConfig.GetEntry<uint32_t>("digitizer", "RecordLength", 1024)),
  fNChannels(32),  // V1742 full range
  fChannelMask(0),
  fNActiveChannels(0),
  fPostTriggerSize(fConfig.GetEntry<uint32_t>("digitizer", "PostTriggerSize", 50)),
  fAcquisitionMode(CAEN_DGTZ_FIRST_TRG_CONTROLLED),
  fNTransferedEvents(1),
  fGroupMask(0),

  fSelfTrigger(fConfig.GetEntry<bool>("digitizer", "SelfTrigger", false)),
  fSaveRaw(fConfig.GetEntry<bool>("digitizer", "SaveRaw", false)),
  fExternalTrigger(fConfig.GetEntry<bool>("digitizer", "ExternalTrigger", true)),
  fSelfTriggerMode(CAEN_DGTZ_TRGMODE_DISABLED),
  fExternalTriggerMode(CAEN_DGTZ_TRGMODE_ACQ_ONLY),
  fPulsePolarity(static_cast<CAEN_DGTZ_PulsePolarity_t>(fConfig.GetEntry<uint32_t>("digitizer", "PulsePolarity", 1))),
  fTriggerPolarity(static_cast<CAEN_DGTZ_TriggerPolarity_t>(fConfig.GetEntry<uint32_t>("digitizer", "TriggerPolarity", 1))),

  fNRMSThreshold(fConfig.GetEntry<double>("digitizer", "NRMSThreshold", 3.0)),
  fIntegralThreshold(fConfig.GetEntry<double>("digitizer", "IntegralThreshold", -100.0)),

  fBuffer(nullptr),
  fBufferSize(0),
  fEventInfo(),
  fVoidEvent(nullptr),
  fEvent(nullptr),
  fEventPtr(nullptr),

  fWaitTimeS(fConfig.GetEntry<double>("digitizer", "WaitTimeS", 3.0)),
  fSamplingTime(0.2e-9),
  fSamplingRateStr(fConfig.GetEntry<std::string>("digitizer", "SamplingRate", "5GHz")),
  fACQT(),
  fDeadT(0.0),
  fNNoiseEvents(fConfig.GetEntry<uint32_t>("digitizer", "NNoiseEvents", 100)),
  fNEvents(fConfig.GetEntry<uint32_t>("digitizer", "NEvents", 100)),
  fDuration(0),

  fOutputFormat(kROOT),
  fOutputDir(fConfig.GetEntry<std::string>("digitizer", "OutputDir", "")),
  fOutputFileName(fConfig.GetEntry<std::string>("digitizer", "OutputFile", "run")),
  fRunNumber(fConfig.GetEntry<int>("digitizer", "RunNumber", 1)),
  fROOTFile(nullptr),
  fChannelTree(nullptr),
  fEventTree(nullptr),
  fASCIIFile(),
 
  fTimestamp_s(0),
  fTimestamp_ns(0),
  fTriggerTime(0)
  {
    // === Lettura ChannelList ===
    std::vector<int64_t> tmpchlist = fConfig.GetEntryList<int64_t>("digitizer","ChannelList", -1, 0);
    if (tmpchlist.empty() || tmpchlist[0] == -1) {
      Log::OutError("No valid 'ChannelList' found in config. You must specify at least one channel.");
      exit(1);
    }

    for (auto& c : tmpchlist) {
      if (c < 0 || c >= 32) {
	Log::OutError("Invalid channel index " + std::to_string(c) + " in ChannelList (must be 0–31)");
	exit(1);
      }
      fChannelList.push_back(static_cast<uint32_t>(c));
    }

    fNActiveChannels = fChannelList.size();
    fChannelMask = 0;
    for (uint32_t ch : fChannelList)
      fChannelMask |= (1 << ch);

    Log::OutSummary("→ Active channels (" + std::to_string(fNActiveChannels) + "):");
    for (auto& ch : fChannelList)
      Log::OutSummary("    ch" + std::to_string(ch));
    //Log::OutSummary("→ ChannelMask = " + IntToHex(fChannelMask));

    fSelfTriggerMode = fSelfTrigger ? CAEN_DGTZ_TRGMODE_ACQ_ONLY : CAEN_DGTZ_TRGMODE_DISABLED;
    fExternalTriggerMode = fExternalTrigger ? CAEN_DGTZ_TRGMODE_ACQ_ONLY : CAEN_DGTZ_TRGMODE_DISABLED;

    std::string outputformat = fConfig.GetEntry<std::string>("digitizer", "OutputFormat", "");
    Log::OutSummary("OutputFormat read from config = '" + outputformat + "'");
    if (outputformat == "ROOT")
      fOutputFormat = kROOT;
    else if (outputformat == "ASCII")
      fOutputFormat = kASCII;
    else if (outputformat == "HDF5")
      fOutputFormat = kHDF5;
    else {
      Log::OutError("Output format " + outputformat + " does not exist. Abort.");
      exit(1);
    }
  }

Digitizer::~Digitizer() {
  Close();
}

void Digitizer::SelectBoard()
{
  CAEN_DGTZ_ErrorCode re = CAEN_DGTZ_OpenDigitizer2(
						    fConnectionType,
						    (void*)fIPAddress.c_str(),
						    fConetNode,
						    fVMEBaseAddress,
						    &fHandle
						    );

  if(re == CAEN_DGTZ_Success)
    {
      Log::OutSummary("Digitizer connected.");
      re = CAEN_DGTZ_GetInfo(fHandle, &fBoardInfo);
      Log::OutSummary("Digitizer model: " + std::string(fBoardInfo.ModelName));
      Log::OutSummary("ROC firmware release: " + std::string(fBoardInfo.ROC_FirmwareRel));
      Log::OutSummary("AMC firmware release: " + std::string(fBoardInfo.AMC_FirmwareRel));
      //Log::OutSummary("Serial number: " + std::to_string(fBoardInfo.SerialNumber));
      // === Imposta il Sampling Rate dal file TOML ===
      CAEN_DGTZ_DRS4Frequency_t drs4Freq = CAEN_DGTZ_DRS4_5GHz;
      double sampling_ns = 0.2e-9;
  
      if (fSamplingRateStr == "5GHz") {
	drs4Freq = CAEN_DGTZ_DRS4_5GHz;
	sampling_ns = 0.2e-9;
      } else if (fSamplingRateStr == "2.5GHz") {
	drs4Freq = CAEN_DGTZ_DRS4_2_5GHz;
	sampling_ns = 0.4e-9;
      } else if (fSamplingRateStr == "1GHz") {
	drs4Freq = CAEN_DGTZ_DRS4_1GHz;
	sampling_ns = 1.0e-9;
      } else {
	Log::OutWarning("Unknown SamplingRate = '" + fSamplingRateStr + "'. Defaulting to 5 GHz.");
      }
  
      CAEN_DGTZ_ErrorCode freqCode = CAEN_DGTZ_SetDRS4SamplingFrequency(fHandle, drs4Freq);
      if (freqCode == CAEN_DGTZ_Success) {
	fSamplingTime = sampling_ns;
	Log::OutSummary("→ Sampling frequency set to " + fSamplingRateStr + " (" + std::to_string(fSamplingTime * 1e9) + " ns per sample)");
      } else {
	Log::OutWarning("→ Failed to set DRS4 sampling frequency (code = " + std::to_string(freqCode) + "). Using default 5 GHz.");
	fSamplingTime = 0.2e-9;
      }
      re = CAEN_DGTZ_LoadDRS4CorrectionData(fHandle, drs4Freq);
      if (re == CAEN_DGTZ_Success){
	Log::OutSummary("→ PLL / DRS4 calibration loaded.");
      }        else{
	Log::OutWarning("→ PLL calibration not supported or failed (code = " + std::to_string(re) + ").");
      }
      CAEN_DGTZ_Calibrate(fHandle);
      Log::OutSummary("PLL calibratiion done.");
    }
  else
    {
      Log::OutError("Cannot connect to the digitizer. Error code: " + std::to_string(re) + ".");
      exit(1);
    }
  
}



void Digitizer::Reset() {
  CAEN_DGTZ_ErrorCode re = CAEN_DGTZ_Reset(fHandle);
  if (re == CAEN_DGTZ_Success)
    Log::OutSummary("Digitizer reset.");
  else {
    Log::OutError("Cannot reset digitizer. Error code: " + std::to_string(re));
    exit(1);
  }
}

void Digitizer::Configure() {
  Log::OutSummary("Configuring digitizer parameters...");

  // Record Length
  CAEN_DGTZ_SetRecordLength(fHandle, fRecordLength);

  // Gruppi: attiva solo i gruppi necessari (0–7)
  fGroupMask = 0;
  for (auto ch : fChannelList) {
    int group = ch / 4;  // ogni gruppo ha 4 canali
    fGroupMask |= (1 << group);
  }
  CAEN_DGTZ_SetGroupEnableMask(fHandle, fGroupMask);

  // Canali abilitati
  CAEN_DGTZ_SetChannelEnableMask(fHandle, fChannelMask);

  // PostTrigger
  CAEN_DGTZ_SetPostTriggerSize(fHandle, fPostTriggerSize);
  //Log::OutDebug("→ PostTrigger size set to " + std::to_string(fPostTriggerSize) + "%");

  // Modalità acquisizione
  CAEN_DGTZ_SetAcquisitionMode(fHandle, CAEN_DGTZ_SW_CONTROLLED);
  //CAEN_DGTZ_SetAcquisitionMode(fHandle, CAEN_DGTZ_S_IN_CONTROLLED);

  // IO Level NIM
  CAEN_DGTZ_SetIOLevel(fHandle, CAEN_DGTZ_IOLevel_NIM);

  // profondita' della FIFO del digitizer
  CAEN_DGTZ_SetMaxNumEventsBLT(fHandle, 2048);
  
  // Trigger Polarity
  for (auto ch : fChannelList)
    CAEN_DGTZ_SetTriggerPolarity(fHandle, ch, fTriggerPolarity);

  // Offset per ogni canale
  for (auto ch : fChannelList) {
    CAEN_DGTZ_SetChannelDCOffset(fHandle, ch, 0x7000);  // segnali negativi
    uint32_t offset = 0;
    CAEN_DGTZ_GetChannelDCOffset(fHandle, ch, &offset);
    // Log::OutDebug("→ DC offset ch" + std::to_string(ch) + " = " + std::to_string(offset));
  }

  

  
#ifdef CAEN_DGTZ_SetCoupling
  for (auto ch : fChannelList) {
    CAEN_DGTZ_CouplingTypes_t coupling;
    CAEN_DGTZ_GetCoupling(fHandle, ch, &coupling);
    std::string ctype = (coupling == CAEN_DGTZ_AC) ? "AC" : "DC";
    //Log::OutDebug("→ Coupling ch" + std::to_string(ch) + " = " + ctype);
  }
#endif

  Log::OutSummary("Digitizer configuration complete.");
}
void Digitizer::GetVMElibVersion() {
  std::cout << "CAEN VMElib version: "
	    << CAENVME_VERSION_MAJOR << "."
	    << CAENVME_VERSION_MINOR << "."
	    << CAENVME_VERSION_PATCH << std::endl;
}

void Digitizer::Close() {
  if (fVoidEvent)
    CAEN_DGTZ_FreeEvent(fHandle, &fVoidEvent);
  if (fBuffer)
    CAEN_DGTZ_FreeReadoutBuffer(&fBuffer);
  if (fHandle)
    CAEN_DGTZ_CloseDigitizer(fHandle);
  fVoidEvent = nullptr;
  fBuffer = nullptr;
  fHandle = 0;
}

void Digitizer::InitAcquisition() {
  CAEN_DGTZ_ErrorCode re;
  re = CAEN_DGTZ_MallocReadoutBuffer(fHandle, &fBuffer, &fBufferSize);
  if (re != CAEN_DGTZ_Success) {
    Log::OutError("Failed to allocate buffer.");
    exit(1);
  }

  re = CAEN_DGTZ_AllocateEvent(fHandle, &fVoidEvent);
  if (re != CAEN_DGTZ_Success) {
    Log::OutError("Failed to allocate event.");
    exit(1);
  }

  fEvent = reinterpret_cast<CAEN_DGTZ_X742_EVENT_t*>(fVoidEvent);

  Log::OutSummary("Acquisition initialized.");
}
void Digitizer::SetTriggerThreshold(double offset)
{
  (void)offset;

  CAEN_DGTZ_ErrorCode re = CAEN_DGTZ_SWStartAcquisition(fHandle);
  if (re != CAEN_DGTZ_Success) {
    Log::OutError("Start acquisition failed.");
    return;
  }
  else {
    Log::OutSummary("Calculating channels baseline...");
  }

  re = CAEN_DGTZ_SendSWtrigger(fHandle);
  if (re != CAEN_DGTZ_Success) {
    Log::OutError("Software trigger failed.");
    return;
  }

  re = CAEN_DGTZ_ReadData(fHandle, CAEN_DGTZ_SLAVE_TERMINATED_READOUT_MBLT, fBuffer, &fBufferSize);
  if (re != CAEN_DGTZ_Success || fBufferSize == 0) {
    Log::OutError("ReadData failed or buffer empty.");
    return;
  }

  uint32_t nEvents = 0;
  re = CAEN_DGTZ_GetNumEvents(fHandle, fBuffer, fBufferSize, &nEvents);
  if (re != CAEN_DGTZ_Success) {
    Log::OutError("GetNumEvents failed.");
    return;
  }

  for (uint32_t i = 0; i < nEvents; i++) {
    re = CAEN_DGTZ_GetEventInfo(fHandle, fBuffer, fBufferSize, i, &fEventInfo, &fEventPtr);
    if (re != CAEN_DGTZ_Success || !fEventPtr) {
      Log::OutError("GetEventInfo failed.");
      continue;
    }

    re = CAEN_DGTZ_DecodeEvent(fHandle, fEventPtr, &fVoidEvent);
    if (re != CAEN_DGTZ_Success) {
      Log::OutError("DecodeEvent failed.");
      continue;
    }

    fEvent = reinterpret_cast<CAEN_DGTZ_X742_EVENT_t*>(fVoidEvent);

    for (uint32_t ch : fChannelList) {
      int group = ch / 4;
      int local_ch = ch % 4;

      if (group >= 4 || local_ch >= 4)
        continue;

      uint32_t nsamples = fEvent->DataGroup[group].ChSize[local_ch];
      float* waveform = fEvent->DataGroup[group].DataChannel[local_ch];
      if (nsamples < MIN_SAMPLES || nsamples > MAX_SAMPLES || waveform == nullptr) {
        //Log::OutDebug("  → canale " + std::to_string(ch) + " skip: nsamples=" + std::to_string(nsamples));
        continue;
      }

      double baseline = 0.0;
      for (uint32_t s = 0; s < nsamples; ++s)
        baseline += waveform[s];
      baseline /= nsamples;

      fBaselineMean[ch] = baseline;
      //      Log::OutDebug("  → ch" + std::to_string(ch) + " baseline = " + std::to_string(baseline));
    }
  }
}

void Digitizer::AcquireEvents() {
  CAEN_DGTZ_ErrorCode re = CAEN_DGTZ_SWStartAcquisition(fHandle);
  if (re != CAEN_DGTZ_Success) {
    Log::OutError("Start acquisition failed.");
    return;
  }

  Log::OutSummary("→ Acquisition started (waiting for external triggers)");
  std::cout << std::endl; // per andare a capo alla fine del run


  // Start timing acquisition
  auto t_start = std::chrono::high_resolution_clock::now();

  uint32_t totalEvents = 0;
  const uint32_t maxEvents = fNEvents;
  const int maxRetries = 5000;
  int retry = 0;

  while (totalEvents < maxEvents && retry < maxRetries) {
    re = CAEN_DGTZ_ReadData(fHandle, CAEN_DGTZ_SLAVE_TERMINATED_READOUT_MBLT, fBuffer, &fBufferSize);
    if (re != CAEN_DGTZ_Success) {
      Log::OutError("ReadData failed.");
      break;
    }

    if (fBufferSize == 0) {
      std::this_thread::sleep_for(std::chrono::milliseconds(1000));
      retry++;
      continue;
    }

    uint32_t nEvents = 0;
    re = CAEN_DGTZ_GetNumEvents(fHandle, fBuffer, fBufferSize, &nEvents);
    if (re != CAEN_DGTZ_Success) {
      Log::OutError("GetNumEvents failed.");
      continue;
    }

    //Log::OutSummary("→ Eventi ricevuti: " + std::to_string(nEvents));

    for (uint32_t j = 0; j < nEvents && totalEvents < maxEvents; j++) {
      re = CAEN_DGTZ_GetEventInfo(fHandle, fBuffer, fBufferSize, j, &fEventInfo, &fEventPtr);
      if (re != CAEN_DGTZ_Success || !fEventPtr) {
        Log::OutError("GetEventInfo failed.");
        continue;
      }

      re = CAEN_DGTZ_DecodeEvent(fHandle, fEventPtr, &fVoidEvent);
      if (re != CAEN_DGTZ_Success) {
        Log::OutError("DecodeEvent failed.");
        continue;
      }

      fEvent = reinterpret_cast<CAEN_DGTZ_X742_EVENT_t*>(fVoidEvent);

      std::vector<int16_t> allSamplesCorr;
      std::vector<uint16_t> allSamplesRaw;

      for (uint32_t ch : fChannelList) {
        int group = ch / 4;
        int local_ch = ch % 4;

        if (group >= 4 || local_ch >= 4)
          continue;

        uint32_t nsamples = fEvent->DataGroup[group].ChSize[local_ch];
        float* waveform = fEvent->DataGroup[group].DataChannel[local_ch];

        if (nsamples < MIN_SAMPLES || nsamples > MAX_SAMPLES || waveform == nullptr) {
	  //  Log::OutDebug("  → ch" + std::to_string(ch) + " [INVALID] nsamples=" + std::to_string(nsamples));
          continue;
        }

        double baseline = fBaselineMean.count(ch) ? fBaselineMean[ch] : 0.0;

        for (uint32_t i = 0; i < nsamples; ++i) {
          float corrected = waveform[i] - baseline;
          allSamplesCorr.push_back(static_cast<int16_t>(corrected));
          if (fSaveRaw)
            allSamplesRaw.push_back(static_cast<uint16_t>(waveform[i]));
        }

	//      Log::OutDebug("  → ch" + std::to_string(ch) + " [OK] nsamples=" + std::to_string(nsamples));
      }

      // Aggiorna in-place il contatore eventi nella stessa riga (senza newline)
      std::cout << "\r→ Events decoded: " << std::setw(6) << totalEvents + 1
		<< "/" << maxEvents << std::flush;

      // === Scrittura HDF5 ===
      if (fOutputFormat == kHDF5 && fH5File != nullptr) {
        try {
          std::string dsname = "/events/event" + std::to_string(totalEvents);
          hsize_t dim_corr = allSamplesCorr.size();
          H5::DataSpace dataspace_corr(1, &dim_corr);
          H5::DataSet ds_corr = fH5Group->createDataSet(dsname, H5::PredType::NATIVE_INT16, dataspace_corr);
          ds_corr.write(allSamplesCorr.data(), H5::PredType::NATIVE_INT16);

          if (fSaveRaw) {
            std::string rawname = "/events_raw/event" + std::to_string(totalEvents);
            hsize_t dim_raw = allSamplesRaw.size();
            H5::DataSpace dataspace_raw(1, &dim_raw);
            H5::Group rawGroup = fH5File->openGroup("/events_raw");
            H5::DataSet ds_raw = rawGroup.createDataSet(rawname, H5::PredType::NATIVE_UINT16, dataspace_raw);
            ds_raw.write(allSamplesRaw.data(), H5::PredType::NATIVE_UINT16);
          }

        } catch (const H5::Exception& e) {
          Log::OutError("HDF5 write error: " + std::string(e.getDetailMsg()));
        }
      }

      totalEvents++;
    }

    retry = 0;
  }

  // Stop timing acquisition
  auto t_end = std::chrono::high_resolution_clock::now();
  std::chrono::duration<double> elapsed = t_end - t_start;
  double elapsed_s = elapsed.count();
  double rate_kHz = (elapsed_s > 0) ? (totalEvents / elapsed_s / 1000.0) : 0.0;

  CAEN_DGTZ_SWStopAcquisition(fHandle);

  std::cout << std::endl; 
  std::cout << std::endl; 
  Log::OutSummary("Acquisition complete.");
  Log::OutSummary("→ Total events recorded: " + std::to_string(totalEvents));
  Log::OutSummary("→ Acquisition time: " + std::to_string(elapsed_s) + " s");
  Log::OutSummary("→ Trigger rate: " + std::to_string(rate_kHz) + " kHz");
 
  CloseOutputFile();
}

long Digitizer::GetTime() {
  struct timeval t1;
  gettimeofday(&t1, nullptr);
  return t1.tv_sec * 1000 + t1.tv_usec / 1000;
}

bool Digitizer::CheckAccepted(std::map<uint32_t,uint32_t>& nAccepted) {
  for (auto& c : fChannelList)
    if (nAccepted[c] < fNNoiseEvents)
      return false;
  return true;
}


void Digitizer::PrepareOutput() {
  if (fOutputFormat != kHDF5)
    return;

  if (!std::filesystem::exists(fOutputDir)) {
    Log::OutError("Output directory does not exist: " + fOutputDir);
    exit(1);
  }

  std::string base = fOutputFileName;
  std::ostringstream fileStream;

  // === Trova il prossimo numero di run, controllando anche i file .h5.gz ===
  int maxRun = -1;
  try {
    for (const auto& entry : std::filesystem::directory_iterator(fOutputDir)) {
      if (!entry.is_regular_file())
        continue;

      std::string name = entry.path().filename().string();

      // Filtra solo file che iniziano con il nome base e contengono ".h5" o ".h5.gz"
      if (name.rfind(base + "_", 0) == 0 &&
          (name.find(".h5") != std::string::npos)) {

        // cerca pattern tipo "_0123"
        size_t pos = name.find("_");
        if (pos != std::string::npos && name.size() >= pos + 5) {
          std::string runStr = name.substr(pos + 1, 4);
          if (std::all_of(runStr.begin(), runStr.end(), ::isdigit)) {
            int runVal = std::stoi(runStr);
            if (runVal > maxRun)
              maxRun = runVal;
          }
        }
      }
    }
  } catch (...) {
    Log::OutWarning("→ Unable to scan existing runs, defaulting to RunNumber = 0.");
  }

  fRunNumber = maxRun + 1;

  // === Costruisci il nome del file ===
  std::string rateTag = "_unkRate";
  if (fSamplingRateStr == "5GHz") rateTag = "_5Gs";
  else if (fSamplingRateStr == "2.5GHz") rateTag = "_2.5Gs";
  else if (fSamplingRateStr == "1GHz") rateTag = "_1Gs";
  else {
    std::string s = fSamplingRateStr;
    s.erase(std::remove_if(s.begin(), s.end(), ::isspace), s.end());
    if (s.find("5") != std::string::npos) rateTag = "_5Gs";
    else if (s.find("2.5") != std::string::npos) rateTag = "_2.5Gs";
    else if (s.find("1") != std::string::npos) rateTag = "_1Gs";
  }

  std::ostringstream postTrigTag;
  postTrigTag << "_" << fPostTriggerSize << "PT";

  fileStream << fOutputDir << "/" << base << "_"
             << std::setw(4) << std::setfill('0') << fRunNumber
             << rateTag << postTrigTag.str() << ".h5";

  fOutputPath = fileStream.str();
  Log::OutSummary("→ HDF5 output path selected: " + fOutputPath);

  // === Creazione file HDF5 ===
  try {
    fH5File = new H5::H5File(fOutputPath, H5F_ACC_TRUNC);
    fH5Group = new H5::Group(fH5File->createGroup("/events"));
    fH5File->createGroup("/events_raw");

    // === Gruppo di configurazione ===
    H5::Group header = fH5File->createGroup("/config");

    header.createAttribute("RunNumber", H5::PredType::NATIVE_INT,
                           H5::DataSpace()).write(H5::PredType::NATIVE_INT, &fRunNumber);
    header.createAttribute("RecordLength", H5::PredType::NATIVE_UINT,
                           H5::DataSpace()).write(H5::PredType::NATIVE_UINT, &fRecordLength);
    header.createAttribute("PostTriggerSize", H5::PredType::NATIVE_UINT,
                           H5::DataSpace()).write(H5::PredType::NATIVE_UINT, &fPostTriggerSize);
    header.createAttribute("SamplingTime", H5::PredType::NATIVE_DOUBLE,
                           H5::DataSpace()).write(H5::PredType::NATIVE_DOUBLE, &fSamplingTime);
    header.createAttribute("SamplingRate", H5::StrType(0, H5T_VARIABLE),
                           H5::DataSpace()).write(H5::StrType(0, H5T_VARIABLE), fSamplingRateStr);
    header.createAttribute("OutputFormat", H5::StrType(0, H5T_VARIABLE),
                           H5::DataSpace()).write(H5::StrType(0, H5T_VARIABLE), std::string("HDF5"));

    std::string trig_mode = fExternalTrigger ? "External" :
                            (fSelfTrigger ? "Self" : "Disabled");
    header.createAttribute("TriggerMode", H5::StrType(0, H5T_VARIABLE),
                           H5::DataSpace()).write(H5::StrType(0, H5T_VARIABLE), trig_mode);

    if (!fChannelList.empty()) {
      hsize_t dim = fChannelList.size();
      H5::DataSpace dspace(1, &dim);
      H5::Attribute chattr = header.createAttribute("ChannelList",
                                                    H5::PredType::NATIVE_UINT, dspace);
      chattr.write(H5::PredType::NATIVE_UINT, fChannelList.data());
    }

  } catch (const H5::Exception& e) {
    Log::OutError("HDF5 file creation failed: " + std::string(e.getDetailMsg()));
    exit(1);
  }
}


void Digitizer::CloseOutputFile() {
  if (fOutputFormat != kHDF5 || fH5File == nullptr)
    return;

  try {
    fH5Group->close();
    fH5File->close();

    delete fH5Group;
    delete fH5File;
    fH5Group = nullptr;
    fH5File = nullptr;

    // Compressione con gzip
    std::string originalFile = fOutputPath;
    std::string gzippedFile = fOutputPath + ".gz";

    Log::OutSummary("→ Compressing HDF5 file: " + originalFile);
    std::string gzipCommand = "gzip -f \"" + originalFile + "\"";
    int ret = std::system(gzipCommand.c_str());

    if (ret == 0) {
      fOutputPath = gzippedFile;
      Log::OutSummary("→ HDF5 file compressed: " + gzippedFile);
    } else {
      Log::OutError("→ Compression failed. Code: " + std::to_string(ret));
    }

    Log::OutSummary("→ HDF5 file closed and cleaned up.");
  } catch (const H5::Exception& e) {
    Log::OutError("→ HDF5 file close failed: " + std::string(e.getDetailMsg()));
  }
}
std::string Digitizer::IntToHex(uint32_t val) {
  std::stringstream stream;
  stream << "0x" << std::hex << std::uppercase << val;
  return stream.str();
}
