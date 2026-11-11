add_executable(DAQ-WC
    DAQ-WC.cpp
)

target_include_directories(DAQ-WC PRIVATE
    ${CMAKE_SOURCE_DIR}/src
    ${CMAKE_SOURCE_DIR}/utils
    ${CMAKE_SOURCE_DIR}/toml
)

target_link_libraries(DAQ-WC PRIVATE daqcomponents)

# DAQ-X2730 solo se src/ ha trovato la CAEN FELib (vedi src/CMakeLists.txt).
if(TARGET daqx2730)
    add_executable(DAQ-X2730 DAQ-X2730.cpp)
    target_link_libraries(DAQ-X2730 PRIVATE daqx2730)
endif()
