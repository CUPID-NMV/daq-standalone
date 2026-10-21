#ifndef MIDASWRITER_H
#define MIDASWRITER_H

// Scrittura di file MIDAS (.mid.gz) senza la libreria MIDAS.
//
// Il formato e' quello documentato da TRIUMF (MidasWiki, "Event Structure")
// e riprodotto byte per byte da un file vero della DAQ di CYGNO
// (Testbeam2026_WC_unified/Data_TB/run01339.mid.gz), perche' lo scopo e' che
// gli strumenti di CYGNO -- wclib.py, i notebook del test beam -- leggano i
// nostri file senza modifiche:
//
//   evento     16 byte: event_id (u16), trigger_mask (u16), serial (u32),
//              time (u32, secondi Unix), data_size (u32), poi i dati
//   BOR/EOR    event_id 0x8000 / 0x8001, trigger_mask 0x494D ("MI"),
//              serial = numero di run, dati = dump dell'ODB in JSON
//   dati       un "bank header" da 8 byte: dimensione di tutti i blocchi
//              (u32) e flag 0x11 (formato 1, blocchi a 32 bit), poi i
//              blocchi: nome (4 caratteri), tipo (u32, TID_xxx), dimensione
//              (u32), dati allineati a 8 byte
//
// Il file si comprime mentre si scrive (zlib, livello 1), come fa il logger
// di MIDAS: e' la compressione degli zeri dei canali non registrati a renderlo
// sostenibile, e a fine run non c'e' un gzip lungo quanto il file.

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <zlib.h>

class MidasWriter {
public:
    // Tipi dei dati nei blocchi, da midas.h
    static constexpr uint32_t TID_WORD  = 4;   // uint16
    static constexpr uint32_t TID_DWORD = 6;   // uint32

    ~MidasWriter() { Close(); }

    bool Open(const std::string& path) {
        Close();
        fFile = gzopen(path.c_str(), "wb1");
        return fFile != nullptr;
    }
    bool IsOpen() const { return fFile != nullptr; }

    void WriteOdb(uint16_t eventId, uint32_t runNumber, uint32_t time, const std::string& json) {
        Header(eventId, 0x494D, runNumber, time, static_cast<uint32_t>(json.size()));
        Write(json.data(), json.size());
    }

    // Un evento di dati si costruisce blocco per blocco, poi si scrive.
    void BeginEvent() { fBanks.clear(); }
    void AddBank(const char name[4], uint32_t type, const void* data, uint32_t bytes) {
        const size_t pos = fBanks.size();
        const uint32_t padded = (bytes + 7u) & ~7u;
        fBanks.resize(pos + 12 + padded, 0);
        std::memcpy(&fBanks[pos], name, 4);
        std::memcpy(&fBanks[pos + 4], &type, 4);
        std::memcpy(&fBanks[pos + 8], &bytes, 4);
        if (bytes) std::memcpy(&fBanks[pos + 12], data, bytes);
    }
    void EndEvent(uint16_t eventId, uint32_t serial, uint32_t time) {
        const uint32_t allBanks = static_cast<uint32_t>(fBanks.size());
        const uint32_t flags = 0x11;
        Header(eventId, 0, serial, time, allBanks + 8);
        Write(&allBanks, 4);
        Write(&flags, 4);
        Write(fBanks.data(), fBanks.size());
    }

    // Rende leggibile ai lettori quanto scritto finora (un blocco gzip
    // chiuso). Costa un po' di compressione: va chiamata di rado.
    void Flush() { if (fFile) gzflush(fFile, Z_SYNC_FLUSH); }

    void Close() {
        if (fFile) { gzclose(fFile); fFile = nullptr; }
    }

    // Stringa JSON con i caratteri di controllo protetti.
    static std::string JsonString(const std::string& s) {
        std::string o = "\"";
        for (unsigned char c : s) {
            switch (c) {
                case '"':  o += "\\\""; break;
                case '\\': o += "\\\\"; break;
                case '\n': o += "\\n";  break;
                case '\r': o += "\\r";  break;
                case '\t': o += "\\t";  break;
                default:
                    if (c < 0x20) { char b[8]; std::snprintf(b, sizeof b, "\\u%04x", c); o += b; }
                    else o += static_cast<char>(c);
            }
        }
        return o + "\"";
    }

private:
    void Header(uint16_t id, uint16_t mask, uint32_t serial, uint32_t time, uint32_t size) {
        unsigned char h[16];
        std::memcpy(h, &id, 2);
        std::memcpy(h + 2, &mask, 2);
        std::memcpy(h + 4, &serial, 4);
        std::memcpy(h + 8, &time, 4);
        std::memcpy(h + 12, &size, 4);
        Write(h, 16);
    }
    void Write(const void* p, size_t n) {
        if (fFile && n) gzwrite(fFile, p, static_cast<unsigned>(n));
    }

    gzFile fFile = nullptr;
    std::vector<unsigned char> fBanks;
};

#endif // MIDASWRITER_H
