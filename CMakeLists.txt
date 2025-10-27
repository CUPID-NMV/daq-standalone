cmake_minimum_required(VERSION 3.10)
project(GAGG-DAQ VERSION 1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ... altre configurazioni ...

add_subdirectory(utils)
add_subdirectory(toml)
add_subdirectory(src)
add_subdirectory(main)    # qui viene aggiunta la cartella main

# Non mettere add_executable qui
