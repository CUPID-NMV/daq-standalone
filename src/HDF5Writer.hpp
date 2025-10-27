#ifndef HDF5WRITER_HPP
#define HDF5WRITER_HPP

#include <H5Cpp.h>
#include <string>
#include <vector>
#include <map>
#include <cstdint>

class HDF5Writer {
public:
    explicit HDF5Writer(const std::string& filename);
    ~HDF5Writer();

    void WriteEvent(uint32_t eventID,
                    const std::map<uint32_t, std::vector<float>>& waveforms,
                    const std::map<uint32_t, double>& baselines,
                    const std::map<uint32_t, uint64_t>& timestamps);

private:
    H5::H5File m_file;
};

#endif // HDF5WRITER_HPP
