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

    // Access to raw VME operations
    uint32_t Read(uint32_t address);
    void Write(uint32_t address, uint32_t data);
    std::string ToHex(uint32_t val);

private:
    std::string fIPAddress;
    int32_t     fHandle;

    Config& fConfig;
};

#endif // BRIDGE_H
