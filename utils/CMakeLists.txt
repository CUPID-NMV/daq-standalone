
# Explicitly list source files in this shared library
# When a new source file is added, it must be added to this list.

# Globbing is not recommended, see: https://cmake.org/cmake/help/v3.12/command/file.html#filesystem

# you can regenerate this list easily by running in this directory: ls -1 *.cc
add_library(daqutils SHARED
  Log.cpp
  Config.cpp
)

message( "source dir detector " ${CMAKE_SOURCE_DIR})
# set top-level directory as include root
target_include_directories(daqutils PUBLIC ${CMAKE_CURRENT_LIST_DIR})

# link this library with ROOT libraries and other Octopus libraries
target_link_libraries(daqutils ${ROOT_LIBRARIES} toml)

# set shared library version equal to project version
set_target_properties(daqutils PROPERTIES VERSION ${PROJECT_VERSION})
