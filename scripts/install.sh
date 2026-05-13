#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${WORKSPACE:-$HOME/ros2_delatometry}"
ROS_SETUP="${ROS_SETUP:-/opt/ros/jazzy/setup.bash}"
VENV_DIR="${VENV_DIR:-$HOME/venvs/ros2_delatometry_webui}"

echo "[webui install] workspace: $WORKSPACE"
echo "[webui install] ROS setup:  $ROS_SETUP"
echo "[webui install] venv:       $VENV_DIR"

if [ ! -f "$ROS_SETUP" ]; then
  echo "ERROR: ROS setup file not found: $ROS_SETUP"
  exit 1
fi

sudo apt update
sudo apt install -y \
  python3-venv \
  python3-pip \
  python3-dev \
  build-essential

python3 -m venv --system-site-packages "$VENV_DIR"

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

python3 -m pip install --upgrade pip setuptools wheel
python3 -m pip install -r "$WORKSPACE/src/webui/requirements.txt"

# shellcheck disable=SC1091
source "$ROS_SETUP"

cd "$WORKSPACE"

colcon build --symlink-install --packages-select webui

# shellcheck disable=SC1091
source "$WORKSPACE/install/setup.bash"

python3 -c "import rclpy; print('rclpy OK')"
python3 -c "import gradio; print('gradio OK')"
python3 -c "from webui.node import main; print('webui import OK')"

echo
echo "[webui install] OK"
echo "Start manually:"
echo "  source $ROS_SETUP"
echo "  source $WORKSPACE/install/setup.bash"
echo "  source $VENV_DIR/bin/activate"
echo "  ros2 launch webui webui.launch.py"