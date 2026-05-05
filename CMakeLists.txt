cmake_minimum_required(VERSION 3.8)
project(webui)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclpy REQUIRED)
find_package(std_msgs REQUIRED)
find_package(msgs REQUIRED)
find_package(core REQUIRED)
find_package(database REQUIRED)

install(PROGRAMS
  webui/run.py
  DESTINATION lib/${PROJECT_NAME}
)

install(DIRECTORY
  webui
  DESTINATION lib/${PROJECT_NAME}
  FILES_MATCHING PATTERN "*.py"
)

install(DIRECTORY
  launch
  config
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
