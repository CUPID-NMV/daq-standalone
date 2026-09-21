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
# Build main executable
# ---------------------------------------------------------------
#add_executable(DAQ-WC main.cpp)

#target_link_libraries(DAQ-WC
#    PRIVATE
#    daqcomponents
#)
