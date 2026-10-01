#ifndef CFD_H
#define CFD_H

#include <cstdint>
#include <string>
#include <vector>

class Config;
class Bridge;

// ---------------------------------------------------------------------------
//  Discriminatore a frazione costante CAEN V812 (16 canali, VME)
//
//  Serve a generare il trigger dall'OR dei canali sopra soglia, come
//  alternativa al self-trigger del V1742. Il vantaggio e' che discrimina il
//  segnale VERO del rivelatore: la soglia si imposta in millivolt, con passo
//  di 1 mV, senza l'attenuazione del Transparent Mode e senza la calibrazione
//  dipendente dalla larghezza dell'impulso.
//
//  Il modulo e' facoltativo: se la sezione [cfd] non c'e' nel TOML, o se
//  Enabled = false, questa classe non tocca il bus e non stampa nulla.
//
//  Riferimento: V812 User's Manual rev. 7 (WEB_V812_rev7.pdf, in root).
// ---------------------------------------------------------------------------
class CFD
{
public:
    CFD( Config& config, Bridge& bridge );

    bool Enabled() const { return fEnabled; }

    // Scrive TUTTI i registri e si ferma se il modulo non risponde come un
    // V812. Da chiamare una volta, prima di iniziare l'acquisizione.
    void Configure();

    // Per l'header HDF5: servono in analisi per sapere a che soglia e' stato
    // preso il dato.
    uint32_t                    BaseAddress() const { return fBase; }
    const std::vector<int64_t>& Channels()    const { return fChannels; }
    const std::vector<int64_t>& ThresholdsMv()const { return fThresholdMv; }

private:
    void ReadConfig();
    void Validate();
    void Identify();            // legge gli identificativi: unico riscontro possibile
    void Write16( uint32_t offset, uint16_t value, const std::string& what );

    Config& fConfig;
    Bridge& fBridge;

    bool     fPresent;          // la sezione [cfd] esiste nel TOML
    bool     fEnabled;
    uint32_t fBase;
    std::string fAMStr;         // "A32" oppure "A24"
    int      fAM;               // CVAddressModifier corrispondente

    std::vector<int64_t> fChannels;     // ingressi V812 collegati
    std::vector<int64_t> fThresholdMv;  // soglie, in mV (modulo: il segnale e' negativo)
    int64_t  fWidth;            // conteggi 0..255
    int64_t  fDeadTime;         // conteggi 0..255
    int64_t  fMajority;         // livello di maggioranza 1..20 (1 = OR)

    // ---- REGISTRI (V812 User's Manual rev.7, Tab. 3.1) ----
    // Parole da 16 bit: i cicli VME devono essere D16, non D32.
    static constexpr uint32_t REG_THRESHOLD_CH0 = 0x00;  // +2 per canale, fino a 0x1E
    static constexpr uint32_t REG_WIDTH_0_7     = 0x40;
    static constexpr uint32_t REG_WIDTH_8_15    = 0x42;
    static constexpr uint32_t REG_DEADTIME_0_7  = 0x44;
    static constexpr uint32_t REG_DEADTIME_8_15 = 0x46;
    static constexpr uint32_t REG_MAJORITY      = 0x48;
    static constexpr uint32_t REG_INHIBIT       = 0x4A;
    static constexpr uint32_t REG_TEST_PULSE    = 0x4C;
    static constexpr uint32_t REG_FIXED_CODE    = 0xFA;  // deve valere 0xFAF5
    static constexpr uint32_t REG_MANUF_TYPE    = 0xFC;

    static constexpr uint16_t V812_FIXED_CODE   = 0xFAF5;
    static constexpr uint16_t V812_MANUFACTURER = 0x02;   // 000010b, bit[15:10]
    static constexpr uint16_t V812_MODULE_TYPE  = 0x51;   // 0001010001b, bit[9:0]

    static constexpr int64_t  N_CHANNELS        = 16;
    static constexpr int64_t  THRESHOLD_MIN_MV  = 5;      // il manuale non garantisce sotto i -5 mV
    static constexpr int64_t  THRESHOLD_MAX_MV  = 255;
    static constexpr uint16_t THRESHOLD_OFF     = 255;    // canale non usato: il piu' insensibile possibile
};

#endif // CFD_H
