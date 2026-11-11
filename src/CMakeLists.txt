cmake_minimum_required(VERSION 3.10)
project(DAQ-WC)

# ---------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------
find_package(HDF5 REQUIRED COMPONENTS C CXX)

# ---------------------------------------------------------------
# Library with DAQ components
# ---------------------------------------------------------------
add_library(daqcomponents
    Digitizer.cpp
    Bridge.cpp
    CFD.cpp
)

# ---------------------------------------------------------------
# Include directories
# ---------------------------------------------------------------
target_include_directories(daqcomponents PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_SOURCE_DIR}/utils
    ${CMAKE_SOURCE_DIR}/toml
    ${HDF5_INCLUDE_DIRS}
    /usr/include/hdf5/serial
)

# ---------------------------------------------------------------
# Link libraries
# ---------------------------------------------------------------
target_link_libraries(daqcomponents
    PUBLIC
    daqutils
    toml
    CAENVME
    CAENComm
    CAENDigitizer
    ${HDF5_LIBRARIES}
    ${HDF5_CXX_LIBRARIES}
    pthread
    dl
    z
)

# ---------------------------------------------------------------
# Compilation flags
# ---------------------------------------------------------------
target_compile_options(daqcomponents PRIVATE
    -Wall -Wextra -O2
)

# ---------------------------------------------------------------
# Digitizer 2.0 (VX2730B) con la CAEN FELib, dentro DAQ-WC: --board
# su una scheda con Model = "VX2730B" usa la classe X2730. Si compila
# solo dove la libreria c'e'. Il PC DAQ del Gran Sasso non ce l'ha:
# li' DAQ-WC si compila esattamente come prima, e una scheda 2.0 si
# rifiuta con un messaggio (DAQ_HAVE_FELIB non definito).
# ---------------------------------------------------------------
find_library(CAEN_FELIB_LIB CAEN_FELib)
find_path(CAEN_FELIB_INC CAEN_FELib.h)
if(CAEN_FELIB_LIB AND CAEN_FELIB_INC)
    message(STATUS "CAEN FELib trovata (${CAEN_FELIB_LIB}): DAQ-WC legge anche i digitizer 2.0")
    target_sources(daqcomponents PRIVATE X2730.cpp)
    target_include_directories(daqcomponents PUBLIC ${CAEN_FELIB_INC})
    target_link_libraries(daqcomponents PUBLIC ${CAEN_FELIB_LIB})
    target_compile_definitions(daqcomponents PUBLIC DAQ_HAVE_FELIB)
else()
    message(STATUS "CAEN FELib non trovata: DAQ-WC senza i digitizer 2.0 (VX2730B)")
endif()

# ---------------------------------------------------------------
# Build main executable
# ---------------------------------------------------------------
#add_executable(DAQ-WC main.cpp)

#target_link_libraries(DAQ-WC
#    PRIVATE
#    daqcomponents
#)
