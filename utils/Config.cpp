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

    const int verbosity = fTbl["settings"]["verbosity"].value_or(3);
    Log::OpenLog(verbosity );
  
    return;
}

void Config::SelectBoard( const std::string& name )
{
    toml::table* boards = fTbl["boards"].as_table();
    toml::table* scheda = boards ? (*boards)[name].as_table() : nullptr;
    if( scheda == nullptr )
	{
	    std::string elenco;
	    if( boards )
		for( auto&& [k, v] : *boards )
		    elenco += " " + std::string(k.str());
	    Log::OutError( "Scheda '" + name + "' non trovata: manca la sezione [boards." + name +
			   "] nel TOML. Schede definite:" + (elenco.empty() ? " nessuna" : elenco) );
	    exit(1);
	}
    if( !(*scheda)["Enabled"].value_or(true) )
	{
	    Log::OutError( "Scheda '" + name + "' disabilitata ([boards." + name +
			   "] Enabled = false): non parte." );
	    exit(1);
	}

    if( !fTbl["digitizer"].as_table() )
	fTbl.insert_or_assign( "digitizer", toml::table{} );
    toml::table* dig = fTbl["digitizer"].as_table();
    for( auto&& [k, v] : *scheda )
	{
	    if( k.str() == "Enabled" )
		continue;
	    v.visit( [&]( auto&& x ){ dig->insert_or_assign( k.str(), x ); } );
	}
    fBoard = name;
    Log::OutSummary( "Scheda: " + name + " (sezione [boards." + name + "] sopra [digitizer])" );
}
