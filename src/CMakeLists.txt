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
# VX2730B (digitizer 2.0) con la CAEN FELib: si compila solo dove la
# libreria c'e'. Il PC DAQ del Gran Sasso non ce l'ha, e DAQ-WC deve
# continuare a compilare li' esattamente come prima.
# ---------------------------------------------------------------
find_library(CAEN_FELIB_LIB CAEN_FELib)
find_path(CAEN_FELIB_INC CAEN_FELib.h)
if(CAEN_FELIB_LIB AND CAEN_FELIB_INC)
    message(STATUS "CAEN FELib trovata (${CAEN_FELIB_LIB}): si compila DAQ-X2730")
    add_library(daqx2730 X2730.cpp)
    target_include_directories(daqx2730 PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}
        ${CMAKE_SOURCE_DIR}/utils
        ${CMAKE_SOURCE_DIR}/toml
        ${HDF5_INCLUDE_DIRS}
        /usr/include/hdf5/serial
        ${CAEN_FELIB_INC}
    )
    target_link_libraries(daqx2730 PUBLIC
        daqutils
        toml
        ${CAEN_FELIB_LIB}
        ${HDF5_LIBRARIES}
        ${HDF5_CXX_LIBRARIES}
        pthread
    )
    target_compile_options(daqx2730 PRIVATE -Wall -Wextra -O2)
else()
    message(STATUS "CAEN FELib non trovata: DAQ-X2730 (VX2730B) non si compila")
endif()

# ---------------------------------------------------------------
# Build main executable
# ---------------------------------------------------------------
#add_executable(DAQ-WC main.cpp)

#target_link_libraries(DAQ-WC
#    PRIVATE
#    daqcomponents
#)
