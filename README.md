# webui

Gradio web interface for the Delatometry ROS 2 stack (replacement for legacy Delphi UI + new operational tools).

## Pages

| Page | Purpose |
|------|---------|
| **Dashboard** (`/`) | General status + Programs (CRUD, run/stop, export) |
| **Experiment** (`/experiment`) | Live LTM2985 temperature + E7-20 streams, manual heater |
| **Configuration** (`/configuration`) | Network, nodes, DB test, core PWM pins, ADS1256 enable |

Default HTTP port is **80** (systemd unit grants `CAP_NET_BIND_SERVICE` so the `pi` user can bind it).

## Features

- **1 s auto-refresh** on Dashboard (General) and Experiment page only
- **Experiment runner** — multi-step temperature ramp via `/core/query`
- **Measurement logging** — during a run, inserts rows into DB (`measurement_insert`) with E7-20 + LTM data
- **E7-20 commands** — publishes `std_msgs/UInt8` on `/measure_device/command` (handled by `measure_device`)
- **E7-20 frequency profile** — per-program config in `program_meta` (modes inspired by legacy Delphi)
- **Export** — ZIP with `program.csv`, `program_steps.csv`, `measurements.csv`, `meta.json`

## Install

```bash
pip install -r src/webui/requirements.txt
colcon build --packages-select measure_device webui --symlink-install
sudo bash src/webui/scripts/install_sudoers.sh   # optional: systemd + nmcli without password
```

## Run

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 launch webui webui.launch.py
```

Open `http://<host>/` (port 80).

## Parameters (`config/webui.params.yaml`)

| Parameter | Default | Notes |
|-----------|---------|-------|
| `enable_measurement_logging` | `true` | DB rows each control period while program runs |
| `measure_command_topic` | `/measure_device/command` | E7-20 byte commands |
| `enable_service_control` | `true` | Requires sudoers for `systemctl` |
| `auth_enabled` | `false` | Set `true` for HTTP basic auth on the UI |

## Core / PWM

Temperature control requires **`enable_pwm_controller:=true`** on the core node (`DELATOMETRY_CORE_ENABLE_PWM_CONTROLLER=true` in `/etc/default/delatometry`). The UI shows a reminder when core is up but PWM is off.

## Sudoers

See `scripts/install_sudoers.sh` or add manually:

```
youruser ALL=(root) NOPASSWD: /bin/systemctl start delatometry-*, /bin/systemctl stop delatometry-*, \
  /bin/systemctl restart delatometry-*, /usr/bin/nmcli
```
