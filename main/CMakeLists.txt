add_executable(DAQ-WC
    DAQ-WC.cpp
)

target_include_directories(DAQ-WC PRIVATE
    ${CMAKE_SOURCE_DIR}/src
    ${CMAKE_SOURCE_DIR}/utils
    ${CMAKE_SOURCE_DIR}/toml
)

target_link_libraries(DAQ-WC PRIVATE daqcomponents)
