#ifndef LOG_H
#define LOG_H

#include <string>
#include <typeinfo>
#include <vector>
#include <iostream>

class Log {

public:
    enum LogLevel {
        nothing,                ///< Print nothing
        error,                  ///< Print only errors
        warning,                ///< Print only warnings and errors
        summary,                ///< Print only results summary, warnings, and errors
        debug                   ///< Print everything, including debug info
    };

    Log();
    Log(const Log::LogLevel& loglevel);
    ~Log();

    static void OpenLog(const Log::LogLevel& loglevel=Log::summary);

    static void OpenLog(const int& loglevel);

    static void SetLogLevel(const Log::LogLevel& loglevel);

    static void Out(const Log::LogLevel& loglevel, const std::string& message="");
    
    static void Out(const std::string& message="") {
        Out(Log::fLogLevel, message);
    }

    static void OutError(const std::string& message="") {
        Out(error, message);
    }

    static void OutWarning(const std::string& message="") {
        Out(warning, message);
    }

    static void OutSummary(const std::string& message="") {
        Out(summary, message);
    }

    static void OutDebug(const std::string& message="") {
        Out(debug, message);
    }

    template<typename T>
    static void Out(const Log::LogLevel& loglevel, const std::vector<T> values)
    {
	if (Log::fLogLevel >= loglevel) {

	    if (loglevel == Log::LogLevel::error) {
		std::cout << "\033[1;31m";
	    } else if (loglevel == Log::LogLevel::warning) {
		std::cout << "\033[1;34m";
	    } else if (loglevel == Log::LogLevel::debug) {
		std::cout << "\033[32m";
	    }
	    std::cout << Log::ToString(loglevel);
	    for( auto it: values )
		std::cout << std::boolalpha << it << "\t";
	    std::cout << "\033[0m"<< std::endl;
	}
	
	return;
    }

    template<typename T>
    static void OutError( const std::vector<T> values ) {
	Out(error, values);
    }

    template<typename T>
    static void OutWarning( const std::vector<T> values ) {
	Out(warning, values);
    }

    template<typename T>
    static void OutSummary( const std::vector<T> values ) {
	Out(summary, values);
    }

    template<typename T>
    static void OutDebug( const std::vector<T> values ) {
	Out(debug, values);
    }
    
    static std::string ToString(const Log::LogLevel&);

    static LogLevel& GetLogLevel() {
        return fLogLevel;
    }

private:
    static Log::LogLevel fLogLevel;

};

#endif
