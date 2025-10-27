#ifndef CONFIG_H
#define CONFIG_H

#include <string>
#include <iostream>
#include <fstream>

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
    
    template <typename T>
    void CheckDefault( std::string category,
		       std::string key,
		       T value,
		       T defvalue )
    {
	if( value == defvalue )
	    Log::OutWarning( "Using default value for [" + category + "][" + key + "]" );
	
	return;
    }
    
    template <typename T>
    T GetEntry( std::string category,
		std::string key,
		T defvalue )
    {
	T value = fTbl[category][key].value_or(defvalue);
	CheckDefault( category, key, value, defvalue );
	return value;
    }

    uint32_t GetTime( std::string category,
		      std::string key,
		      toml::time defvalue )
    {
	toml::time value = fTbl[category][key].value_or(defvalue);
	CheckDefault( category, key, value, defvalue );
	
	return 3600*value.hour + 60*value.minute + value.second;
    }

    template <typename Tin, typename Tout>
    void CheckDefault( std::string category,
		       std::string key,
		       Tout value,
		       Tin defvalue )
    {
	if( value == static_cast<Tout>(defvalue) )
	    Log::OutWarning("Using default value for [" + category + "][" + key + "]" );
	return;
    }
    
    template <typename Tin, typename Tout>
    Tout GetEntry( std::string category,
		   std::string key,
		   Tin defvalue )
    {
	Tout value = static_cast<Tout>(std::stod(fTbl[category][key].value_or(defvalue)));
	CheckDefault<Tin,Tout>( category, key, value, defvalue );
	return value;
    }

    template <typename T>
    void CheckDefault( std::string category,
		       std::string subcategory,
		       std::string key,
		       T value,
		       T defvalue )
    {
	if( value == defvalue )
	    Log::OutWarning( "Using default value for [" + category + "][" + subcategory + "][" + key + "]" );
	
	return;
    }

    template <typename T>
    T GetSubEntry( std::string category,
		   std::string subcategory,
		   std::string key,
		   T defvalue )
    {
	T value = fTbl[category][subcategory][key].value_or(defvalue);
	CheckDefault( category, subcategory, key, value, defvalue);
	return value;
    }

    template <typename Tin, typename Tout>
    void CheckDefault( std::string category,
		       std::string subcategory,
		       std::string key,
		       Tout value,
		       Tin defvalue )
    {
	if( value == static_cast<Tout>(defvalue) )
	    Log::OutWarning( "Using default value for [" + category + "][" + subcategory + "][" + key + "]" );
	return;
    }
    
    template <typename Tin, typename Tout>
    Tout GetSubEntry( std::string category,
		      std::string subcategory,
		      std::string key,
		      Tin defvalue )
    {
	Tout value = static_cast<Tout>(std::stod(fTbl[category][subcategory][key].value_or(defvalue)));
	CheckDefault<Tin,Tout>( category, subcategory, key, value, defvalue );
	return value;
    }

    template <typename T>
    void CheckDefault( std::string category,
		       std::string key,
		       std::vector<T>& value,
		       T defvalue )
    {
	size_t nequal = 0;
	for( auto it: value )
	    if( it == defvalue )
		nequal++;
	if( nequal == value.size() )
	    Log::OutWarning( "Using default value for [" + category + "][" + key + "]" );
	return;
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
		    value.emplace_back( static_cast<T>( *(arr->get_as<T>(i))) );
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
	    for( size_t i=0; i<size; i++ )
		value.emplace_back(defvalue);

	if( value.size() > 0 )
	    CheckDefault( category, key, value, defvalue );

	return value;
    }

    template <typename T>
    void CheckDefault( std::string category,
		       std::string subcategory,
		       std::string key,
		       std::vector<T>& value,
		       T defvalue )
    {
	size_t nequal = 0;
	for( auto it: value )
	    if( it == defvalue )
		nequal++;
	if( nequal == value.size() )
	    Log::OutWarning( "Using default value for [" + category + "][" + subcategory + "][" + key + "]" );
	return;
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
		    value.emplace_back( static_cast<T>( *(arr->get_as<T>(i))) );
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
	    for( size_t i=0; i<size; i++ )
		value.emplace_back(defvalue);

	if( value.size() > 0 )
	    CheckDefault( category, subcategory, key, value, defvalue );

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
			last = static_cast<T>( *(arr->get_as<T>(i)));
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
			last = static_cast<T>( *(arr->get_as<T>(i)));
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
