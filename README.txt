# DAQ Standalone — Data Acquisition System for CAEN V1742

This project implements a complete data acquisition (DAQ) system for the **CAEN V1742 (DRS4-based) digitizer**.  
It supports dynamic channel selection, HDF5 output, baseline correction, DRS4 sampling frequency configuration, and automatic run numbering.

---

```
daq-standalone/
│
├── config/
│   └── template-daq.toml       # DAQ configuration file
│
├── src/                        # Main source code
│   ├── Digitizer.cpp
│   ├── Digitizer.h
│   ├── Log.h
│   └── main.cpp
│
├── utils/
│   └── Config.h                # TOML parser and configuration loader
│
├── data/                       # Output directory for .h5.gz files
└── build/                      # Build directory (created during compilation)
```

---

The DAQ requires:

- **CMake** (≥ 3.10)
- **g++** with C++17 support
- **CAEN Digitizer SDK** (libCAENDigitizer)
- **HDF5 C++ library** (libhdf5_cpp)
- **toml++** (included in the project)
- **zlib** for gzip compression

Make sure the CAEN libraries are correctly installed and visible (e.g., in `/usr/lib` or `/opt/CAEN`).

---

## 🔧 How to Build the Project (Clean Build)

This is the recommended procedure.

```bash
cd ~/daq-standalone
rm -rf build
mkdir build
cd build
cmake ..
make -j4
```

After successful compilation, run the DAQ with:

```bash
./main/DAQ-WC ../config/template-daq.toml
```

---

## 📝 Configuration (TOML)

All DAQ parameters are controlled via the file:

```
config/template-daq.toml
```

Key sections include:

- **Connection settings** (IP, VME base address)
- **ChannelList** (dynamic selection of channels)
- **SamplingRate** (“5GHz”, “2.5GHz”, “1GHz”)
- **Number of events** and acquisition timing
- **Output options** (HDF5 / RAW)

Example:

```toml
[digitizer]
ChannelList = [0, 1, 2, 3]
SamplingRate = "1GHz"
RecordLength = 1024
PostTriggerSize = 90
NEvents = 10
OutputFormat = "HDF5"
OutputDir = "/home/daq/daq-standalone/data"
OutputFile = "WC_proto"
```

---

## 📁 Output Files

The DAQ produces compressed HDF5 files:

```
WC_proto_0007_1Gs_90PT.h5.gz
```

The filename encodes:

- Run number (auto-incremented)
- Sampling rate tag (`1Gs`, `2.5Gs`, `5Gs`)
- Post-trigger percentage

Each file contains two groups:

- `/events` — baseline-corrected waveforms  
- `/events_raw` — raw waveforms (if enabled)

Metadata is stored under:

- `/config` — includes sampling time, record length, enabled channels, trigger mode.

---

## ▶️ Running the DAQ

```bash
./main/DAQ-WC ../config/template-daq.toml
```

During acquisition, the terminal shows a live event counter updated on a single line:

```
→ Events decoded:   148/1000
```

At the end of a run, the system:

1. Stops the digitizer  
2. Writes metadata to the file  
3. Compresses the `.h5` file into `.h5.gz`  
4. Reports timing and event rate

---

## 🧪 Testing and Debugging

To increase verbosity, enable debug messages in `Log.h`:

```cpp
#define LOG_DEBUG 1
```

You may also enable printouts inside the waveform decoding loop.

---

## 💾 Notes on Automatic Run Numbering

The DAQ scans the `OutputDir` (e.g., `data/`) for files matching:

```
<OutputFile>_XXXX_*.h5  or  .h5.gz
```

It determines the highest existing run number and uses the next one.

This ensures:

- The run number always increments  
- The user does **not** need to edit the TOML  
- Open editors do not interfere with the numbering  
- `.h5.gz` files are correctly detected  

---

