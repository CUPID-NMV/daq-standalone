
#include <iostream>
#include <sstream>
#include <cstdint>
#include "Log.h"
#include "Bridge.h"
#include "Config.h"

Bridge::Bridge(Config& config)
  : fHandle(-1)
  , fConfig(config)
{
  //    fIPAddress = fConfig.GetString("bridge", "IPAddress", "192.168.99.105");
    fIPAddress = fConfig.GetEntry<std::string>("bridge", "IPAddress", "192.168.99.105");
    Log::OutDebug("Bridge IP address set to: " + fIPAddress);
}

    Bridge::~Bridge() {
    Close();
}

void Bridge::Open() {
    CVBoardTypes bdType = cvETH_V4718_LOCAL;
    CVErrorCodes ret = CAENVME_Init2(bdType, fIPAddress.c_str(), 0, &fHandle);
char fwRelease[100];
if (CAENVME_BoardFWRelease(fHandle, fwRelease) == cvSuccess) {
Log::OutSummary("Bridge firmware version: " + std::string(fwRelease));
} else {
    Log::OutWarning("Unable to get bridge firmware version.");
}


    if (ret != cvSuccess) {
        throw std::runtime_error("Failed to initialize VME bridge: " + std::string(CAENVME_DecodeError(ret)));
    }
    std::cout << "[INFO] Bridge connected at " << fIPAddress << ", handle: " << fHandle << std::endl;
}

void Bridge::Close() {
    if (fHandle >= 0) {
        CAENVME_End(fHandle);
        fHandle = -1;
        std::cout << "[INFO] Bridge disconnected." << std::endl;
    }
}

uint32_t Bridge::Read(uint32_t address) {
    uint32_t data = 0;
    CVErrorCodes ret = CAENVME_ReadCycle(fHandle, address, &data, cvA32_U_DATA, cvD32);
    if (ret != cvSuccess) {
        throw std::runtime_error("Read error at address 0x" + ToHex(address) + ": " + CAENVME_DecodeError(ret));
    }
    return data;
}

void Bridge::Write(uint32_t address, uint32_t data) {
    CVErrorCodes ret = CAENVME_WriteCycle(fHandle, address, &data, cvA32_U_DATA, cvD32);
    if (ret != cvSuccess) {
        throw std::runtime_error("Write error at address 0x" + ToHex(address) + ": " + CAENVME_DecodeError(ret));
    }
}

std::string Bridge::ToHex(uint32_t val) {
    std::stringstream ss;
    ss << std::hex << std::uppercase << val;
    return ss.str();
}

int Bridge::GetHandle() const {
    return fHandle;
}



void Bridge::StartPulser()
{
    SetPulser();
    CVErrorCodes re = CAENVME_StartPulser( fHandle,
					   CVPulserSelect::cvPulserA );
    if( re==0 )
	Log::OutDebug("Pulser started");
    else
	{
	    Log::OutDebug("Cannot start pulser. Error code: " + std::to_string(re) + "." );
	    exit(1);
	}
    
    return;
}

void Bridge::StopPulser()
{
    CVErrorCodes re = CAENVME_StopPulser( fHandle,
					   CVPulserSelect::cvPulserA );
    if( re==0 )
	Log::OutDebug("Pulser stopped");
    else
	{
	    Log::OutDebug("Cannot stop pulser. Error code: " + std::to_string(re) + "." );
	    exit(1);
	}
    
    return;
}

void Bridge::SetPulser()
{

    // Configure pulser
    CVErrorCodes re = CAENVME_SetPulserConf( fHandle,
    					     CVPulserSelect::cvPulserA,
					     160,
					     208,
					     CVTimeUnits::cvUnit25us,
					     0,
					     CVIOSources::cvManualSW,
					     CVIOSources::cvManualSW );
    if( re==0 )
	Log::OutDebug("Pulser set");
    else
	{
	    Log::OutDebug("Cannot set pulser. Error code: " + std::to_string(re) + "." );
	    exit(1);
	}

    // Set pulser polarity
    //re = CAENVME_SetOutputConf( fHandle,
    //				fPulserChannel,
    //				fPulserPolarity,
    //				CVLEDPolarity::cvActiveHigh,
    //				fIOSource );
    
    return;
}
