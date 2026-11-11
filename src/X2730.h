#ifndef X2730_H
#define X2730_H

// ---------------------------------------------------------------------------
//  VX2730B (digitizer 2.0: 16 canali, 14 bit, 500 MS/s), letto con la CAEN
//  FELib e la sua implementazione Dig2.
//
//  Una classe a parte dal Digitizer del V1742, apposta: libreria, parametri e
//  modello dei dati sono un'altra cosa (niente DRS4, niente gruppi, niente
//  correzioni per cella, niente Transparent Mode), e mescolarli avrebbe
//  riempito di "se e' un 2730" un codice che sul V1742 funziona e che si
//  prova solo al Gran Sasso.
//
//  Il file HDF5 invece e' lo stesso formato del V1742 (v3): /events/waveforms
//  con i canali concatenati in una riga per evento, e gli stessi attributi in
//  /config. Cosi' daqio, i notebook e il monitor lo leggono senza cambiare.
//  In piu' /events/timestamp, a 64 bit in unita' di 8 ns: il trigger_time_tag
//  del V1742 conta a 17 ns su 31 bit, e chi lo rilegge ne darebbe per
//  scontate le unita'.
//
//  La configurazione sta nella sezione [vx2730] del TOML: le chiavi del V1742
//  in [digitizer] qui non valgono (i DCOffset, per esempio, sono percentuali
//  e non conteggi di un DAC).
// ---------------------------------------------------------------------------

#include <atomic>
#include <cstdint>
#include <string>
#include <vector>

#include <H5Cpp.h>

#include "Config.h"

class X2730 {
public:
    explicit X2730(Config& cfg);
    ~X2730();

    void Open();            // collegamento e informazioni sulla scheda
    void Configure();       // canali, finestra, offset, guadagno, trigger
    void PrepareOutput();   // numero di run, .log, file HDF5
    void Acquire();         // ciclo di lettura fino a NEvents, MaxTime o Ctrl-C
    void Close();           // ferma la scheda, chiude file e collegamento

private:
    Config& fConfig;

    // --- configurazione ---
    std::string fUrl;
    std::string fBoard;
    std::string fOutputDir;
    std::string fOutputFile;
    bool        fSaveData = true;
    uint32_t    fNEvents = 0;           // 0 = nessun limite
    double      fMaxTime = 0.0;         // secondi, 0 = nessun limite
    std::vector<uint32_t> fChannels;
    uint32_t    fRecordLength = 1024;   // campioni
    uint32_t    fPreTrigger = 256;      // campioni
    std::vector<double> fDCOffset;      // percento, uno per canale di fChannels
    std::vector<double> fGain;          // dB, uno per canale di fChannels
    std::string fPolarity = "positive";
    std::string fTriggerSourceFelib;    // per /par/AcqTriggerSource
    std::string fTriggerMode;           // per l'HDF5, coi nomi del V1742
    bool        fSoftwareTrigger = false;
    bool        fSelfTrigger = false;
    double      fSWTriggerRate = 100.0;
    std::string fIOLevel = "NIM";
    std::vector<uint32_t> fSelfChannels;
    std::vector<int32_t>  fSelfThreshold;   // modulo, in conteggi sopra/sotto la base
    std::string fSelfLogic = "OR";
    uint32_t    fSelfMajority = 2;
    uint32_t    fFlushEvery = 1;

    // --- scheda ---
    uint64_t    fDev = 0;
    uint64_t    fEp = 0;
    bool        fOpen = false;
    std::string fModel;
    std::string fFwType;
    std::string fFpgaFw;
    uint32_t    fSerial = 0;
    uint32_t    fNumCh = 0;
    uint32_t    fAdcBits = 0;
    double      fSamplingNs = 2.0;
    std::vector<double> fAdcToVolts;    // uno per canale di fChannels

    // --- uscita ---
    int         fRunNumber = 0;
    std::string fOutputPath;
    H5::H5File* fFile = nullptr;
    H5::DataSet* fWaveforms = nullptr;
    H5::DataSet* fCounter = nullptr;
    H5::DataSet* fTimestamp = nullptr;
    hsize_t     fRows = 0;
    hsize_t     fWidth = 0;
    bool        fSwmr = false;

    // --- FELib ---
    void        Set(const std::string& path, const std::string& value);
    std::string Get(const std::string& path, bool fatal = true);
    std::string LastError() const;
    void        Fail(const std::string& what) const;

    // --- aiuti ---
    template <typename T>
    std::vector<T> ScalarOrList(const std::string& key, T def, size_t n);
    void LogBoardStatus(const std::string& when);
    void Flush();
    void CloseOutputFile();
};

#endif  // X2730_H
