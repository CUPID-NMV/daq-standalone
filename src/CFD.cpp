#include <cmath>
#include <iomanip>
#include <set>
#include <sstream>
#include <stdexcept>

#include "CAENVMElib.h"

#include "CFD.h"
#include "Bridge.h"
#include "Config.h"
#include "Log.h"

namespace {

std::string Hex( uint32_t v, int width )
{
    std::ostringstream oss;
    oss << "0x" << std::hex << std::uppercase << std::setfill('0')
        << std::setw(width) << v;
    return oss.str();
}

} // namespace

CFD::CFD( Config& config, Bridge& bridge )
  : fConfig(config)
  , fBridge(bridge)
  , fPresent(false)
  , fEnabled(false)
  , fBase(0)
  , fAMStr("A32")
  , fAM(cvA32_U_DATA)
  , fWidth(0)
  , fDeadTime(0)
  , fMajority(1)
{
    // La sezione e' facoltativa: se manca non si legge nulla e non si avvisa
    // di nulla, altrimenti ogni run senza CFD stamperebbe una pioggia di
    // "using the built-in default" per opzioni che non interessano a nessuno.
    fPresent = fConfig.GetTbl().contains("cfd");
    if( !fPresent )
        return;

    ReadConfig();
}

void CFD::ReadConfig()
{
    auto node = fConfig.GetTbl()["cfd"]["Enabled"];
    fEnabled = node ? node.value_or(false) : false;

    if( !fEnabled )
        {
            Log::OutSummary("→ CFD V812: sezione [cfd] presente ma Enabled = false, modulo ignorato.");
            return;
        }

    // L'indirizzo di base e' fissato dai rotary switch sul modulo: non esiste
    // un default sensato, e sbagliarlo si manifesta come "il trigger non
    // arriva" invece che come un errore. Quindi e' obbligatorio.
    auto base = fConfig.GetTbl()["cfd"]["BaseAddress"];
    if( !base )
        {
            Log::OutError("[cfd] BaseAddress non specificato. E' l'indirizzo impostato sui "
                          "rotary switch del V812 e non ha un default: va scritto nel TOML. Abort.");
            exit(1);
        }
    fBase = static_cast<uint32_t>( base.value_or<int64_t>(0) );

    fAMStr      = fConfig.GetEntry<std::string>("cfd", "AddressModifier", "A32");
    fWidth      = fConfig.GetEntry<int64_t>("cfd", "Width", 0);
    fDeadTime   = fConfig.GetEntry<int64_t>("cfd", "DeadTime", 0);
    fMajority   = fConfig.GetEntry<int64_t>("cfd", "Majority", 1);
    fChannels   = fConfig.GetEntryList<int64_t>("cfd", "Channels", 0, 0);
    fThresholdMv= fConfig.GetEntryList<int64_t>("cfd", "Threshold", 20,
                                                fChannels.empty() ? 1 : fChannels.size());

    Validate();
}

void CFD::Validate()
{
    if( fAMStr == "A32" )      fAM = cvA32_U_DATA;
    else if( fAMStr == "A24" ) fAM = cvA24_U_DATA;
    else
        {
            Log::OutError("[cfd] AddressModifier = \"" + fAMStr + "\" non riconosciuto "
                          "(ammessi: \"A32\", \"A24\"). Abort.");
            exit(1);
        }

    // Le linee A09-A15 del V812 non sono collegate (manuale §3.1): l'indirizzo
    // di base vive nei bit alti, e uno con i bit bassi sporchi e' quasi
    // sicuramente un refuso che altrimenti funzionerebbe "per alias".
    if( fBase == 0 || (fBase & 0xFFFFu) != 0 )
        {
            Log::OutError("[cfd] BaseAddress = " + Hex(fBase, 8) + " non valido: deve essere "
                          "diverso da zero e multiplo di 0x10000 (i 16 bit bassi sono "
                          "l'offset dei registri). Abort.");
            exit(1);
        }

    if( fChannels.empty() )
        {
            Log::OutError("[cfd] Channels e' vuoto: nessun ingresso del V812 da abilitare. Abort.");
            exit(1);
        }

    std::set<int64_t> visti;
    for( auto ch : fChannels )
        {
            if( ch < 0 || ch >= N_CHANNELS )
                {
                    Log::OutError("[cfd] canale " + std::to_string(ch) + " fuori range: il V812 "
                                  "ha 16 ingressi (0-15). Abort.");
                    exit(1);
                }
            if( !visti.insert(ch).second )
                {
                    Log::OutError("[cfd] canale " + std::to_string(ch) + " ripetuto in Channels. Abort.");
                    exit(1);
                }
        }

    for( size_t i=0; i<fChannels.size(); i++ )
        {
            int64_t thr = fThresholdMv[i];
            if( thr < THRESHOLD_MIN_MV || thr > THRESHOLD_MAX_MV )
                {
                    Log::OutError("[cfd] soglia " + std::to_string(thr) + " mV sul canale " +
                                  std::to_string(fChannels[i]) + " fuori range: ammesse da " +
                                  std::to_string(THRESHOLD_MIN_MV) + " a " +
                                  std::to_string(THRESHOLD_MAX_MV) + " mV (il valore e' il "
                                  "modulo, il segnale e' negativo). Abort.");
                    exit(1);
                }
        }

    if( fWidth < 0 || fWidth > 255 || fDeadTime < 0 || fDeadTime > 255 )
        {
            Log::OutError("[cfd] Width e DeadTime sono conteggi da 0 a 255. Abort.");
            exit(1);
        }

    if( fMajority < 1 || fMajority > 20 )
        {
            Log::OutError("[cfd] Majority = " + std::to_string(fMajority) +
                          " fuori range: ammessi da 1 a 20 (1 = OR). Abort.");
            exit(1);
        }
}

void CFD::Write16( uint32_t offset, uint16_t value, const std::string& what )
{
    try
        {
            fBridge.Write( fBase + offset, value,
                           static_cast<CVAddressModifier>(fAM), cvD16 );
        }
    catch( const std::exception& e )
        {
            Log::OutError("CFD V812: scrittura fallita su " + what + " (" +
                          Hex(fBase + offset, 8) + "): " + e.what());
            exit(1);
        }
    Log::OutDebug("   CFD " + Hex(fBase + offset, 8) + " <- " + Hex(value, 4) + "   " + what);
}

void CFD::Identify()
{
    // I registri di configurazione sono tutti write-only: gli identificativi
    // sono l'unico riscontro possibile che ci sia davvero un V812 a questo
    // indirizzo. Senza questo controllo un indirizzo sbagliato non darebbe
    // nessun errore, solo un trigger che non arriva mai.
    uint32_t code = 0, manuf = 0;
    try
        {
            code  = fBridge.Read( fBase + REG_FIXED_CODE,
                                  static_cast<CVAddressModifier>(fAM), cvD16 );
            manuf = fBridge.Read( fBase + REG_MANUF_TYPE,
                                  static_cast<CVAddressModifier>(fAM), cvD16 );
        }
    catch( const std::exception& e )
        {
            Log::OutError("CFD V812: nessuna risposta a " + Hex(fBase, 8) + " (" + e.what() + ").");
            Log::OutError("  Controlla l'indirizzo impostato sui rotary switch del modulo e "
                          "che sia acceso nel crate.");
            exit(1);
        }

    if( (code & 0xFFFFu) != V812_FIXED_CODE )
        {
            Log::OutError("CFD V812: a " + Hex(fBase, 8) + " c'e' qualcosa, ma il codice fisso "
                          "vale " + Hex(code & 0xFFFFu, 4) + " invece di " +
                          Hex(V812_FIXED_CODE, 4) + ": non e' un V812. Abort.");
            exit(1);
        }

    const uint16_t costruttore = (manuf >> 10) & 0x3F;
    const uint16_t tipo        =  manuf        & 0x3FF;
    if( costruttore != V812_MANUFACTURER || tipo != V812_MODULE_TYPE )
        {
            Log::OutError("CFD: il modulo a " + Hex(fBase, 8) + " dichiara costruttore " +
                          std::to_string(costruttore) + " e tipo " + std::to_string(tipo) +
                          ", attesi " + std::to_string(V812_MANUFACTURER) + " e " +
                          std::to_string(V812_MODULE_TYPE) + ". Abort.");
            exit(1);
        }

    Log::OutSummary("→ CFD V812 riconosciuto a " + Hex(fBase, 8) + " (" + fAMStr + ")");
}

void CFD::Configure()
{
    if( !fEnabled )
        return;

    // Il V812 e' uno slave VME: senza un master sul bus non e' raggiungibile.
    // Sul percorso CONET diretto (A4818 -> digitizer) quel master non c'e',
    // perche' il V1742 non puo' pilotare il bus.
    if( fBridge.GetHandle() < 0 )
        {
            Log::OutError("[cfd] Enabled = true ma il bridge VME non e' aperto.");
            Log::OutError("  Il V812 e' uno slave VME e serve il V4718 per parlarci: con "
                          "Connection = \"USB_A4818\" la fibra va dritta al digitizer e il bus "
                          "non ha nessun master. Usa Connection = \"ETH_V4718\" oppure "
                          "disabilita il CFD. Abort.");
            exit(1);
        }

    Identify();

    // All'accensione i registri del V812 sono INDETERMINATI (manuale §4.2):
    // non si puo' assumere nessun default, va scritto tutto. In particolare i
    // canali non usati vanno messi alla soglia piu' alta E disabilitati, se no
    // un ingresso scollegato puo' oscillare e sporcare l'OR.
    uint16_t inibizione = 0;
    for( int64_t ch=0; ch<N_CHANNELS; ch++ )
        {
            uint16_t soglia = THRESHOLD_OFF;
            for( size_t i=0; i<fChannels.size(); i++ )
                if( fChannels[i] == ch )
                    {
                        soglia = static_cast<uint16_t>( fThresholdMv[i] );
                        inibizione |= static_cast<uint16_t>(1u << ch);
                    }
            Write16( REG_THRESHOLD_CH0 + 2*static_cast<uint32_t>(ch), soglia,
                     "soglia ch" + std::to_string(ch) );
        }

    Write16( REG_INHIBIT,       inibizione,                         "pattern di inibizione" );
    Write16( REG_WIDTH_0_7,     static_cast<uint16_t>(fWidth),      "larghezza ch0-7" );
    Write16( REG_WIDTH_8_15,    static_cast<uint16_t>(fWidth),      "larghezza ch8-15" );
    Write16( REG_DEADTIME_0_7,  static_cast<uint16_t>(fDeadTime),   "tempo morto ch0-7" );
    Write16( REG_DEADTIME_8_15, static_cast<uint16_t>(fDeadTime),   "tempo morto ch8-15" );

    // MAJTHR = NINT[(MAJLEV*50 - 25)/4]  (manuale §3.7)
    const uint16_t majthr =
        static_cast<uint16_t>( std::lround( (fMajority * 50.0 - 25.0) / 4.0 ) );
    Write16( REG_MAJORITY, majthr, "soglia di maggioranza (livello " +
                                   std::to_string(fMajority) + ")" );

    std::ostringstream canali;
    for( size_t i=0; i<fChannels.size(); i++ )
        canali << (i ? ", " : "") << "ch" << fChannels[i] << " a -" << fThresholdMv[i] << " mV";

    Log::OutSummary("→ CFD V812 configurato: " + canali.str());
    Log::OutSummary("→ CFD V812: inibizione " + Hex(inibizione, 4) + ", larghezza " +
                    std::to_string(fWidth) + ", tempo morto " + std::to_string(fDeadTime) +
                    " (conteggi), maggioranza " + std::to_string(fMajority));
    Log::OutSummary("→ CFD V812: i registri sono write-only, quindi questi valori sono quelli "
                    "scritti, non riletti dal modulo.");
}
