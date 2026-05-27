cmake_minimum_required(VERSION 3.8)
project(webui)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

find_package(rclpy REQUIRED)
find_package(std_msgs REQUIRED)
find_package(msgs REQUIRED)
find_package(database REQUIRED)

# Install full Python package (webui/*.py, webui/routes/, …) into site-packages.
ament_python_install_package(${PROJECT_NAME}
  PACKAGE_DIR webui
)

install(PROGRAMS
  webui/run.py
  DESTINATION lib/${PROJECT_NAME}
)

install(DIRECTORY
  launch
  config
  DESTINATION share/${PROJECT_NAME}
)

install(DIRECTORY
  webui/templates
  webui/static
  webui/locale
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
