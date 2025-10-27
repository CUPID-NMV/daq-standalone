add_executable(GAGG-DAQ
    GAGG-DAQ.cpp
)

target_include_directories(GAGG-DAQ PRIVATE
    ${CMAKE_SOURCE_DIR}/src
    ${CMAKE_SOURCE_DIR}/utils
    ${CMAKE_SOURCE_DIR}/toml
)

target_link_libraries(GAGG-DAQ PRIVATE daqcomponents)
