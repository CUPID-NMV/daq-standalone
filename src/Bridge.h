#ifndef BRIDGE_H
#define BRIDGE_H

#include <string>
#include <cstdint>
#include "CAENVMElib.h"

class Config; // forward declaration

class Bridge {
public:
    Bridge(Config& config);
    ~Bridge();

    // VME Bridge connection
    void Open();
    void Close();
    int GetHandle() const;

    // Pulser control (test pulser on the V4718)
    void SetPulser();
    void StartPulser();
    void StopPulser();

    // Access to raw VME operations.
    //
    // Ampiezza e address modifier sono parametri perche' non tutti i moduli
    // del crate parlano allo stesso modo: il V1742 usa cicli D32, il V812 ha
    // registri da 16 bit e vuole D16. I default riproducono il comportamento
    // storico, quindi le chiamate esistenti non cambiano.
    uint32_t Read(uint32_t address,
                  CVAddressModifier am = cvA32_U_DATA,
                  CVDataWidth width = cvD32);
    void Write(uint32_t address, uint32_t data,
               CVAddressModifier am = cvA32_U_DATA,
               CVDataWidth width = cvD32);
    std::string ToHex(uint32_t val);

private:
    std::string fIPAddress;
    int32_t     fHandle;

    Config& fConfig;
};

#endif // BRIDGE_H
