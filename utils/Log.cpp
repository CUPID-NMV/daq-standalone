#include "Log.h"

Log::LogLevel Log::fLogLevel = Log::summary;
std::ofstream Log::fFile;
std::vector<std::string> Log::fPending;

void Log::ToFile(const std::string& line) {
    if (fFile.is_open()) {
        fFile << line << std::endl;
    } else {
        // Finche' il file non esiste si tiene tutto da parte. Il limite evita
        // che una run senza file di log si mangi memoria all'infinito.
        if (fPending.size() < 5000) fPending.push_back(line);
    }
}

void Log::OpenFile(const std::string& path) {
    if (fFile.is_open()) fFile.close();
    fFile.open(path, std::ios::out | std::ios::trunc);
    if (!fFile.is_open()) {
        Log::OutWarning("Cannot open the log file " + path + ": the log will stay on screen only.");
        return;
    }
    for (const auto& l : fPending) fFile << l << std::endl;
    fPending.clear();
    Log::OutSummary("→ Log written to " + path);
}

void Log::CloseFile() {
    if (fFile.is_open()) fFile.close();
}

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
        Log::ToFile(Log::ToString(loglevel) + message);
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
