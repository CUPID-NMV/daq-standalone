#include "HDF5Writer.hpp"
#include <sstream>

HDF5Writer::HDF5Writer(const std::string& filename)
    : m_file(filename, H5F_ACC_TRUNC)
{}

HDF5Writer::~HDF5Writer() {
    m_file.close();
}

void HDF5Writer::WriteEvent(uint32_t eventID,
                            const std::map<uint32_t, std::vector<float>>& channelData,
                            const std::map<uint32_t, double>& baselines,
                            const std::map<uint32_t, uint64_t>& timestamps) {
    std::ostringstream groupPath;
    groupPath << "/events/event" << eventID;
    std::string groupName = groupPath.str();

    H5::Group eventGroup = m_file.createGroup(groupName);

    // Write waveforms
    for (const auto& [ch, waveform] : channelData) {
        std::ostringstream name;
        name << "ch" << ch;

        hsize_t dims[1] = { waveform.size() };
        H5::DataSpace dataspace(1, dims);
        H5::DataSet dataset = eventGroup.createDataSet(name.str(), H5::PredType::NATIVE_FLOAT, dataspace);
        dataset.write(waveform.data(), H5::PredType::NATIVE_FLOAT);
    }

    // Write baselines
    for (const auto& [ch, baseline] : baselines) {
        std::ostringstream name;
        name << "ch" << ch << "_baseline";

        hsize_t dims[1] = { 1 };
        H5::DataSpace dataspace(1, dims);
        H5::DataSet dataset = eventGroup.createDataSet(name.str(), H5::PredType::NATIVE_DOUBLE, dataspace);
        dataset.write(&baseline, H5::PredType::NATIVE_DOUBLE);
    }

    // Write timestamps
    for (const auto& [ch, ts] : timestamps) {
        std::ostringstream name;
        name << "ch" << ch << "_timestamp";

        hsize_t dims[1] = { 1 };
        H5::DataSpace dataspace(1, dims);
        H5::DataSet dataset = eventGroup.createDataSet(name.str(), H5::PredType::NATIVE_UINT64, dataspace);
        dataset.write(&ts, H5::PredType::NATIVE_UINT64);
    }
}
