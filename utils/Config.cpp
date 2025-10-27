#include "Config.h"

Config::Config(const bool& readconfig):
fReadConfigFile(readconfig)
{
    if( !fReadConfigFile )
	Log::OpenLog(Log::LogLevel::debug);
}

Config::~Config()
{
    ;
}

void Config::Read( char* filename )
{
    if( !fReadConfigFile )
	{
	    Log::OutError( "Config::Read(): Config files disabled for this executable. Aborting." );
	    exit(1);
	}
    
    fFileName = filename;
    Log::OutDebug( "Reading file " + std::string(fFileName) );

    fTbl = toml::parse_file(fFileName);
    std::cout << fTbl << std::endl;

    const int verbosity = fTbl["settings"]["verbosity"].value_or(3);
    Log::OpenLog(verbosity );
  
    return;
}
