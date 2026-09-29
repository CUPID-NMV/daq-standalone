#ifndef CONFIG_H
#define CONFIG_H

#include <string>
#include <iostream>
#include <fstream>
#include <optional>

#include "Log.h"
#include "toml.hpp"

class Config
{
public:
  
    static Config& GetInstance(const bool& readconfig=true)
    {
	static Config instance(readconfig);
	return instance;
    }

    Config(const bool& readconfig);
    ~Config();
    
    const std::string& GetFileName(){ return fFileName; };

    void SetNoConfigFile();
    bool ConfigFileProvided(){ return fReadConfigFile; };
    void Read( char* filename );
    toml::table& GetTbl(){ return fTbl; };

    bool CheckIfEntryExists( std::string category, bool throwerror=true )
    {
	std::ifstream configfile( fFileName.c_str() );
	std::string line;
	bool found = false;
	while( std::getline( configfile, line ) )
	    {
		if( line[0] == '#' )
		    continue;
		if( line.find(category) != std::string::npos )
		    found = true;
	    }
	if( throwerror && found == false )
	    {
		Log::OutError( "Category [" + category + "] not found in config file. Abort.");
		exit(1);
	    }
	
	return found;
    }

    bool CheckIfSubEntryExists( std::string category, std::string subcategory, bool throwerror=true )
    {
	std::ifstream configfile( fFileName.c_str() );
	std::string line;
	bool found = false;
	while( std::getline( configfile, line ) )
	    {
		if( line[0] == '#' )
		    continue;
		if( line.find( category + "." + subcategory) != std::string::npos )
		    found = true;
	    }
	if( throwerror && found == false )
	    {
		Log::OutError( "Category [" + category + "." + subcategory + "] not found in config file. Abort.");
		exit(1);
	    }
	
	return found;
    }

    bool CheckIfSubSubEntryExists( std::string category, std::string subcategory, std::string subsubcategory, bool throwerror=true )
    {
	std::ifstream configfile( fFileName.c_str() );
	std::string line;
	bool found = false;
	while( std::getline( configfile, line ) )
	    {
		if( line[0] == '#' )
		    continue;
		if( line.find( category + "." + subcategory + "." + subsubcategory) != std::string::npos )
		    found = true;
	    }
	if( throwerror && found == false )
	    {
		Log::OutError( "Category [" + category + "." + subcategory + "." + subsubcategory + "] not found in config file. Abort.");
		exit(1);
	    }
	
	return found;
    }
    
    // Avvisa quando una chiave MANCA, non quando il suo valore coincide con il
    // default. Il controllo precedente confrontava i valori, quindi una chiave
    // scritta esplicitamente nel file faceva scattare "Using default value"
    // solo perche' il valore corrispondeva: il messaggio diceva il falso, e ne
    // uscivano quindici per ogni avvio, che e' il modo migliore per non
    // leggerne piu' nessuno.
    void WarnMissing( const std::string& category,
		      const std::string& key )
    {
	Log::OutWarning( "[" + category + "][" + key + "] not in the config file: "
			 "using the built-in default" );
    }

    template <typename T>
    T GetEntry( std::string category,
		std::string key,
		T defvalue )
    {
	auto node = fTbl[category][key];
	if( !node )
	    {
		WarnMissing( category, key );
		return defvalue;
	    }
	return node.value_or(defvalue);
    }

    uint32_t GetTime( std::string category,
		      std::string key,
		      toml::time defvalue )
    {
	auto node = fTbl[category][key];
	if( !node ) WarnMissing( category, key );
	toml::time value = node ? node.value_or(defvalue) : defvalue;
	
	return 3600*value.hour + 60*value.minute + value.second;
    }

    template <typename Tin, typename Tout>
    Tout GetEntry( std::string category,
		   std::string key,
		   Tin defvalue )
    {
	auto node = fTbl[category][key];
	if( !node ) WarnMissing( category, key );
	return static_cast<Tout>(std::stod(node ? node.value_or(defvalue) : defvalue));
    }

    void WarnMissing( const std::string& category,
		      const std::string& subcategory,
		      const std::string& key )
    {
	Log::OutWarning( "[" + category + "][" + subcategory + "][" + key + "] not in the "
			 "config file: using the built-in default" );
    }

    template <typename T>
    T GetSubEntry( std::string category,
		   std::string subcategory,
		   std::string key,
		   T defvalue )
    {
	auto node = fTbl[category][subcategory][key];
	if( !node )
	    {
		WarnMissing( category, subcategory, key );
		return defvalue;
	    }
	return node.value_or(defvalue);
    }

    template <typename Tin, typename Tout>
    Tout GetSubEntry( std::string category,
		      std::string subcategory,
		      std::string key,
		      Tin defvalue )
    {
	auto node = fTbl[category][subcategory][key];
	if( !node ) WarnMissing( category, subcategory, key );
	return static_cast<Tout>(std::stod(node ? node.value_or(defvalue) : defvalue));
    }

    // Legge un elemento di una lista del TOML.
    //
    // get_as<T>() richiede che il tipo del nodo coincida esattamente e
    // restituisce nullptr altrimenti: dereferenziarlo, come si faceva prima,
    // significa che un semplice [8.0, 9] al posto di [8, 9] fa crashare il
    // programma senza dire niente. value<T>() invece converte fra interi e
    // decimali quando la conversione e' lecita, e quando non lo e' si ottiene
    // un messaggio comprensibile invece di un segfault.
    template <typename T>
    T GetArrayElement( toml::array* arr, size_t i, const std::string& where )
    {
	auto node = arr->get(i);
	std::optional<T> v = node ? node->value<T>() : std::nullopt;
	if( !v )
	    {
		Log::OutError( "Option " + where + ": element " + std::to_string(i) +
			       " has an unexpected type (check integers, decimals and"
			       " quotes). Abort." );
		exit(1);
	    }
	return *v;
    }

    template <typename T>
    std::vector<T> GetEntryList( std::string category,
				 std::string key,
				 T defvalue,
				 size_t size=1 )
    {
	std::vector<T> value;
	
	toml::array* arr = fTbl[category][key].as_array();

	if( arr != nullptr )
	    {
		for( size_t i=0; i<arr->size(); i++ )
		    value.emplace_back( GetArrayElement<T>( arr, i, category + "." + key ) );
		if( arr->size() < size )
		    {
			Log::OutDebug( "[" + category + "][" + key + "]: Defaulting last " +
				       std::to_string(size-arr->size()) + " values." );
			T last = value.back();
			for( size_t i=arr->size(); i<size; i++ )
			    value.emplace_back(last);
		    }
	    }
	else
	    {
		WarnMissing( category, key );
		for( size_t i=0; i<size; i++ )
		    value.emplace_back(defvalue);
	    }

	return value;
    }

    template <typename T>
    std::vector<T> GetSubEntryList( std::string category,
				    std::string subcategory,
				    std::string key,
				    T defvalue,
				    size_t size=1 )
    {
	std::vector<T> value;
	
	toml::array* arr = fTbl[category][subcategory][key].as_array();

	if( arr != nullptr )
	    {
		for( size_t i=0; i<arr->size(); i++ )
		    value.emplace_back( GetArrayElement<T>( arr, i,
							    category + "." + subcategory + "." + key ) );
		if( arr->size() < size )
		    {
			Log::OutDebug( "[" + category + "][" + subcategory + "][" + key + "]: Defaulting last " +
				       std::to_string(size-arr->size()) + " values." );
			T last = value.back();
			for( size_t i=arr->size(); i<size; i++ )
			    value.emplace_back(last);
		    }
	    }
	else
	    {
		WarnMissing( category, subcategory, key );
		for( size_t i=0; i<size; i++ )
		    value.emplace_back(defvalue);
	    }

	return value;
    }

    template <typename T>
    void CheckDefault( std::string category,
		       std::string subcategory,
		       std::string key,
		       std::map<int64_t, T>& value,
		       T defvalue )
    {
	size_t nequal = 0;
	for( auto it: value )
	    if( it.second == defvalue )
		nequal++;
	if( nequal == value.size() )
	    Log::OutWarning( "Using default value for [" + category + "][" + subcategory + "][" + key + "]" );
	return;
    }
    
    template <typename T>
    std::map<int64_t,T> GetSubEntryMap( std::string category,
					     std::string subcategory,
					     std::string key,
					     T defvalue,
					     std::vector<int64_t> channels )
    {
	std::map<int64_t,T> value;
	
	toml::array* arr = fTbl[category][subcategory][key].as_array();

	if( arr != nullptr )
	    {
		if( arr->size() > channels.size() )
		    {
			Log::OutError( "Option " + category + "." + subcategory + "-->" + key + ": number of values larger than number of channels. Abort." );
			exit(1);
		    }

		T last;
		for( size_t i=0; i<arr->size(); i++ )
		    {
			last = GetArrayElement<T>( arr, i, category + "." + key );
			value[channels[i]] = last;
		    }
		
		if( arr->size() < channels.size() )
		    {
			Log::OutDebug( "[" + category + "][" + subcategory + "][" + key + "]: Defaulting last " +
				       std::to_string(channels.size()-arr->size()) + " values." );

			for( size_t i=arr->size(); i<channels.size(); i++ )
			    value[channels[i]] = last;
		    }
	    }
	else
	    for( size_t i=0; i<channels.size(); i++ )
		value[channels[i]] = defvalue;

	CheckDefault( category, subcategory, key, value, defvalue );

	return value;
    }

    template <typename T>
    void CheckDefault( std::string category,
		       std::string subcategory,
		       std::string subsubcategory,
		       std::string key,
		       std::map<int64_t, T>& value,
		       T defvalue )
    {
	size_t nequal = 0;
	for( auto it: value )
	    if( it.second == defvalue )
		nequal++;
	if( nequal == value.size() )
	    Log::OutWarning( "Using default value for [" + category + "][" + subcategory + "][" + subsubcategory + "][" + key + "]" );
	return;
    }
    
    template <typename T>
    std::map<int64_t,T> GetSubSubEntryMap( std::string category,
					   std::string subcategory,
					   std::string subsubcategory,
					   std::string key,
					   T defvalue,
					   std::vector<int64_t> channels )
    {

	std::map<int64_t,T> value;
	
	toml::array* arr = fTbl[category][subcategory][subsubcategory][key].as_array();

	if( arr != nullptr )
	    {
		if( arr->size() > channels.size() )
		    {
			Log::OutError( "Option " + category + "." + subcategory + "-->" + key + ": number of values larger than number of channels. Abort." );
			exit(1);
		    }

		T last;
		for( size_t i=0; i<arr->size(); i++ )
		    {
			last = GetArrayElement<T>( arr, i, category + "." + key );
			value[channels[i]] = last;
		    }
		
		if( arr->size() < channels.size() )
		    {
			Log::OutDebug( "[" + category + "][" + subcategory + "][" + subsubcategory + "][" + key + "]: Defaulting last " +
				       std::to_string(channels.size()-arr->size()) + " values." );

			for( size_t i=arr->size(); i<channels.size(); i++ )
			    value[channels[i]] = last;
		    }
	    }
	else
	    for( size_t i=0; i<channels.size(); i++ )
		value[channels[i]] = defvalue;

	CheckDefault( category, subcategory, subsubcategory, key, value, defvalue );

	return value;
    }

    

    
private:

    std::string fFileName;
    const bool fReadConfigFile;
    toml::table fTbl;

};

#endif
