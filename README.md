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
- **Trigger source** (external TRG-IN and/or channel self-trigger)
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

## ⚡ Self-Trigger (auto-trigger)

Besides the external trigger on **TRG-IN**, the V1742 can trigger on its own
input signals: each channel discriminates its input against a programmable
threshold (leading edge) and the logic OR of the enabled channels generates the
acquisition trigger. Set `SelfTrigger = true` to use it.

```toml
[digitizer]
SelfTrigger              = true
ExternalTrigger          = false
TriggerPolarity          = 1            # 0 = rising edge, 1 = falling edge

SelfTriggerMode          = "global"     # "global" | "paired"
SelfTriggerChannels      = [0, 1, 2, 3] # optional, defaults to ChannelList
SelfTriggerThresholdMode = "relative"   # "relative" | "absolute"
SelfTriggerThreshold     = [2000]       # ADC counts, used in "absolute" mode
SelfTriggerThresholdOffset = 100        # ADC counts, used in "relative" mode
```

**`SelfTriggerMode`**

| Value | Behaviour | Requires |
|---|---|---|
| `paired` | The OR of the channels of groups 0/1 makes groups 0 and 1 acquire; the same, independently, for groups 2/3. | AMC firmware ≥ 0.4 |
| `global` | Over-threshold signals are routed to the motherboard, which ORs them into a single global trigger for the **whole board**. | firmware ≥ 4.30_1.08 |

`SelfTriggerChannels` selects which channels take part in the OR — a channel can
trigger without being read out, and vice versa. Channels listed here but missing
from `ChannelList` are flagged with a warning at startup.

**`SelfTriggerThresholdMode`**

The self-trigger logic discriminates the samples read by the ADC while the DRS4
is in *Transparent Mode*, so the threshold is expressed in **12-bit ADC counts
(0–4095) referred to the transparent-mode waveform**, not to the values stored
in the output file.

- `absolute` — the values of `SelfTriggerThreshold` are written as they are
  (one per self-trigger channel; the last one is replicated over the remaining
  channels).
- `relative` — the DAQ switches the board to Transparent Mode, measures the
  pedestal of each channel with 10 software triggers, then puts the threshold
  `SelfTriggerThresholdOffset` counts away from it, in the direction given by
  `TriggerPolarity` (falling → `baseline − offset`, rising → `baseline +
  offset`). The measured baselines and the resulting thresholds are printed at
  startup. If the measurement fails, the `SelfTriggerThreshold` values are used
  as a fallback.

> ⚠️ **Sampling rate.** The self-trigger path goes through the ADC and the FPGA,
> which costs ~320 ns of latency (~420 ns in `global` mode). It is therefore
> **not usable at 5 GHz** (where the whole acquisition window is ~200 ns), and
> `global` mode is not usable at 2.5 GHz either. Use 1 GHz or 750 MHz. The DAQ
> prints a warning when the configured combination is not compliant.

> ⚠️ **Pulse width.** The transparent-mode ADC runs at ~30 MHz, so pulses
> shorter than ~30 ns may not be sampled and will not fire the self-trigger.

Self-trigger and external trigger are independent and can be enabled together;
if both are disabled the board only acquires on software triggers.

Registers involved (see *UM5698 — 742 Registers Description*): `0x1n80`
(per-channel threshold), `0x1nA8` (per-group channel trigger mask), `0x8000`
bits [13] / [21] / [31:28], `0x810C` bits [3:0].

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
  When the self-trigger is active it also stores `SelfTriggerMode`,
  `SelfTriggerChannels` and the `SelfTriggerThreshold` actually applied.

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

