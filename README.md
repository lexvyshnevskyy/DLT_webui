# webui

Gradio-based ROS 2 web interface for experiment control.

Main features:
- show latest LTM measurements
- list programs from database
- create programs and program steps
- start / stop experiment execution
- periodic step scheduler updates the Core temperature setpoint

Runtime requirements:
- Python package `gradio`
- ROS 2 services `/database/query` and `/core/query`
