#include "Log.h"

Log::LogLevel Log::fLogLevel = Log::summary;

Log::Log() {
    fLogLevel = Log::summary;
}

Log::Log(const Log::LogLevel& loglevel) {
    fLogLevel = loglevel;
}

Log::~Log() {
}

void Log::OpenLog(const Log::LogLevel& loglevel) {

    Log::SetLogLevel(loglevel);

    return;
}

void Log::OpenLog(const int& loglevel) {
    Log::SetLogLevel(static_cast<LogLevel>(loglevel));

    return;
}

void Log::SetLogLevel(const Log::LogLevel& loglevel) {

    fLogLevel = loglevel;

    return;
}

void Log::Out(const Log::LogLevel& loglevel, const std::string& message) {

    if (Log::fLogLevel >= loglevel) {

        if (loglevel == Log::LogLevel::error) {
            std::cout << "\033[1;31m";
        } else if (loglevel == Log::LogLevel::warning) {
            std::cout << "\033[1;34m";
        } else if (loglevel == Log::LogLevel::debug) {
            std::cout << "\033[32m";
        }
        std::cout << Log::ToString(loglevel) << std::boolalpha << message;
        std::cout << "\033[0m"<< std::endl;
    }

}


std::string Log::ToString(const Log::LogLevel& loglevel) {

    switch (loglevel) {
    case debug: {
        return "Debug   : ";
    }
    case summary: {
        return "Summary : ";
    }
    case warning: {
        return "Warning : ";
    }
    case error: {
        return "Error   : ";
    }
    default: {
        return "";
    }
    }
}
