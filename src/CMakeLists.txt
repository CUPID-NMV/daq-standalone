cmake_minimum_required(VERSION 3.10)
project(GAGG_DAQ)

find_package(ROOT REQUIRED)
find_package(HDF5 REQUIRED COMPONENTS C CXX)

# Definisci esplicitamente i nomi delle librerie CAEN
#set(CAENVME_LIB CAENVMElib)
set(CAENDGTZ_LIB CAENDigitizer)
set(CAENVME_LIB "CAENVME")
add_library(daqcomponents
    Digitizer.cpp
    Bridge.cpp
    HDF5Writer.cpp
)

target_include_directories(daqcomponents PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_SOURCE_DIR}/utils
    ${CMAKE_SOURCE_DIR}/toml
    /home/daq/root_build/include
    ${HDF5_INCLUDE_DIRS}
    /usr/include/hdf5/serial
    ${ROOT_INCLUDE_DIRS}
)
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
    ${ROOT_LIBRARIES}
)



target_compile_options(daqcomponents PRIVATE
    -Wall -Wextra -O2
)
