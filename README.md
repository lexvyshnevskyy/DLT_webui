# webui

FastAPI + Jinja2 web interface for the Delatometry ROS 2 stack (replacement for legacy Delphi UI + Gradio prototype).

## Stack

- **FastAPI** + **uvicorn** on port **80**
- **Jinja2** templates + static CSS/JS
- **WebSocket** `/ws/experiment` for live experiment page (Chart.js)

## Pages

| Route | Purpose |
|-------|---------|
| `/dashboard` | System status, services, disks, UART, network, log |
| `/programs` | Program list with Edit / Export / Delete |
| `/program-new` | Create program + draft temperature steps |
| `/program-view?id=N` | View program, steps, E7-20 config, stats |
| `/program-edit?id=N` | Edit program, steps, start/stop run |
| `/experiment` | Live LTM + E7-20 streams, manual heater target |
| `/configuration` | Network, serial ports, DB, core PWM, ADS1256 |

Default HTTP port is **80** (systemd unit grants `CAP_NET_BIND_SERVICE`).

## Features

- **WebSocket refresh** on Experiment page (~`status_refresh_period_sec`)
- **Experiment runner** — multi-step temperature ramp via `/core/query`
- **Measurement logging** — during a run, inserts rows into DB
- **E7-20 sweep** — per-program config in DB
- **Export** — ZIP with program data and measurements
- **HTTP Basic auth** when `auth_enabled:=true`

## Install

```bash
pip install -r src/webui/requirements.txt
colcon build --packages-select measure_device webui --symlink-install
```

After build, the launch entrypoint must exist:

`install/webui/lib/webui/run.py`

If systemd logs `executable 'run.py' not found`, rebuild the package (stale or failed install).

```bash
sudo bash src/webui/scripts/install_sudoers.sh   # optional: systemd + nmcli without password
```

## Run

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 launch webui webui.launch.py
```

Open `http://<host>/dashboard` (root redirects from `/`).

## Parameters (`config/webui.params.yaml`)

| Parameter | Default | Notes |
|-----------|---------|-------|
| `enable_measurement_logging` | `true` | DB rows each control period while program runs |
| `measure_command_topic` | `/measure_device/command` | E7-20 byte commands |
| `enable_service_control` | `true` | Requires sudoers for `systemctl` |
| `auth_enabled` | `false` | HTTP Basic auth when `true` |

## Core / PWM

Temperature control requires **`DELATOMETRY_CORE_ENABLE_PWM_CONTROLLER=true`** in `/etc/default/delatometry`. The dashboard shows a reminder when core is up but PWM is off.

## Sudoers

See `scripts/install_sudoers.sh` or add manually:

```
youruser ALL=(root) NOPASSWD: /bin/systemctl start delatometry-*, /bin/systemctl stop delatometry-*, \
  /bin/systemctl restart delatometry-*, /usr/bin/nmcli
```
