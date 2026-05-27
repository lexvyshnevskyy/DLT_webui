# webui — browser HMI (FastAPI)

Web-based human–machine interface for the Delatometry ROS 2 stack. Replaces the legacy Delphi desktop UI and an earlier Gradio prototype.

> **Note:** This package is the **browser HMI** (`delatometry-webui.service`, port 80). The separate ROS package [`hmi`](../hmi/) drives the **Nextion serial display**; it does not expose these HTTP routes.

## Stack

| Component | Role |
|-----------|------|
| **FastAPI** + **uvicorn** | HTTP server (`bind_port`, default **80**) |
| **Jinja2** | Server-rendered pages |
| **Static** (`/static/`) | CSS, JS (dashboard, programs, experiment) |
| **WebSocket** | Live dashboard + experiment streams |
| **ROS 2 node** `webui` | `WebHMINode` — DB/core/measurement integration in `webui/node.py` |

Entry point after install: `install/webui/lib/webui/run.py`  
Launch: `ros2 launch webui webui.launch.py`

---

## Pages (GET)

| Route | Description |
|-------|-------------|
| `/` | Redirect → `/dashboard` |
| `/dashboard` | System status, host stats, systemd units, disk, UART, network, log |
| `/programs` | Program list (edit, export ZIP, delete) |
| `/program-new` | Create program wizard (description → steps → E7-20). Use `?new=1` to clear draft |
| `/program-view?id=N` | Read-only program detail, steps, E7-20 JSON, measurement stats |
| `/program-edit?id=N` | Edit program, autosave steps, save meta, start/stop run |
| `/experiment` | Live LTM + E7-20 (Chart.js), manual heater target |
| `/configuration` | Network, env file, LTM/measure/DB/core/ADS1256, topic peek |

Query parameters:

- `msg` — flash message on redirect (URL-encoded).
- `lang` — one-shot locale (`en` or `uk`); persisted via cookie when using `/set-locale/…`.
- `iface` — selected network interface on `/configuration`.

---

## API & live data

| Route | Method | Response | Description |
|-------|--------|----------|-------------|
| `/dashboard/snapshot` | GET | JSON | Dashboard live payload (~1 Hz polling fallback) |
| `/api/dashboard/snapshot` | GET | JSON | Alias of `/dashboard/snapshot` |
| `/ws/dashboard` | WebSocket | JSON ~1 Hz | Dashboard live update |
| `/ws/experiment` | WebSocket | JSON | Experiment page (LTM, E7-20, core, measurements) |
| `/program-new/validate` | POST | JSON | Validate new-program form (description, steps, E7-20) |
| `/program-new/steps/save-one` | POST | JSON | Autosave one draft step (`step_id`, `t_start`, `t_stop`, `minutes`) |
| `/program-edit/steps/save-one` | POST | JSON | Autosave one DB step (`id`, `step_id`, fields) |

Static assets: `/static/app.css`, `dashboard.js`, `program_new.js`, `program_edit.js`, `program_steps.js`, `experiment.js`, …

---

## Actions (POST)

Unless noted, successful POSTs redirect (303) back to the referring page with `?msg=…`.

### Global

| Route | Form fields | Effect |
|-------|-------------|--------|
| `/set-locale/{en\|uk}` | — | Set language cookie (`delatometry_lang`), redirect to `?next=` or `Referer` |

Aliases: `ua` → `uk`.

### Dashboard

| Route | Fields | Effect |
|-------|--------|--------|
| `/dashboard/service` | `unit`, `action` (`start` \| `stop` \| `restart`) | `systemctl` via passwordless sudo |

### Programs

| Route | Fields | Effect |
|-------|--------|--------|
| `/program-new` | `description`, `sweep_mode`, `enabled_freqs[]`, `range_max`, `step_*` | Create program in DB |
| `/program-new/steps/add` | `t_start`, `t_stop`, `minutes`, `description` (optional) | Append draft step (server-side draft) |
| `/program-new/steps/remove` | `step_id` | Remove draft step |
| `/program-edit` | `id`, `description`, `sweep_mode`, `enabled_freqs[]`, `range_max`, `step_*` | Save program meta + bulk step edits |
| `/program-edit/steps/add` | `id`, `t_start`, `t_stop`, `minutes` | Insert step in DB |
| `/program-edit/steps/remove` | `id`, `step_id` | Delete step in DB |
| `/program-edit/run` | `id` | Start experiment runner for program |
| `/program-edit/stop` | `id` | Stop running program |
| `/programs/delete` | `id` | Delete program and measurements |
| `/programs/export` | GET `?id=N` | Download ZIP archive |

**New program wizard:** temperature steps must be **40–1600 K**, chained (step *N* `t_start` = step *N−1* `t_stop`). Validated in UI and on create.

### Experiment

| Route | Fields | Effect |
|-------|--------|--------|
| `/experiment/manual` | `target_k`, `enabled` (`true`/`false`) | Manual core temperature target |

### Configuration

| Route | Purpose |
|-------|---------|
| `/configuration/reload` | Reload env snapshot in UI |
| `/configuration/network/select` | `iface` — select interface |
| `/configuration/network/up` | `iface` — bring up |
| `/configuration/network/down` | `iface` — bring down |
| `/configuration/network/dhcp` | `iface` — DHCP |
| `/configuration/network/static` | `iface`, `address`, `prefix`, `gateway`, `dns` |
| `/configuration/network/wifi-scan` | `iface` — scan Wi‑Fi |
| `/configuration/network/wifi-connect` | `iface`, `ssid`, `password` |
| `/configuration/network/hotspot-enable` | Enable hotspot |
| `/configuration/network/hotspot-disable` | Disable hotspot |
| `/configuration/network/vpn-save` | `provider`, `vpn_enabled`, `connect_on_boot`, `connect_now`, ZeroTier ID, OpenVPN credentials |
| `/configuration/network/vpn-upload` | Upload `.ovpn` client profile |
| `/configuration/network/vpn-connect` | Connect VPN now |
| `/configuration/network/vpn-disconnect` | Disconnect VPN |
| `/configuration/ltm` | `port`, `baudrate`, `restart` → `/etc/default/delatometry` |
| `/configuration/measure` | `port`, `speed`, `restart` |
| `/configuration/database/test` | `host`, `port`, `name`, `user`, `password` |
| `/configuration/database` | DB settings + `auto_init`, `restart` |
| `/configuration/core` | `pwm_ch1`, `pwm_ch2`, `enable_db_client`, `enable_pwm`, `restart` |
| `/configuration/ads` | `enabled`, `simulate`, `fallback`, `restart` |
| `/configuration/peek/ltm` | One-shot LTM topic peek on config page |
| `/configuration/peek/e720` | E7-20 topic peek |
| `/configuration/peek/ads` | ADS1256 topic peek |
| `/configuration/peek/hmi` | HMI topic peek |

---

## Localization (i18n)

| Item | Detail |
|------|--------|
| Primary | English (`en`) |
| Secondary | Ukrainian (`uk`, shown as **UA** in UI) |
| Files | `webui/locale/en.json`, `webui/locale/uk.json` |
| Switch | Header **EN** / **UA**, or `/set-locale/en`, `/set-locale/uk` |
| Cookie | `delatometry_lang` (1 year) |

Templates use `{{ _('key') }}`. Validation messages use keys `validation.*`.

---

## CLI / helper scripts

| Script | Purpose |
|--------|---------|
| `scripts/install.sh` | Venv, `colcon build` webui (+ measure_device), verify `run.py` |
| `scripts/install_sudoers.sh` | Passwordless `systemctl` / `nmcli` for dashboard + configuration |
| `scripts/verify_temperature_steps.py` | Validate program JSON (same rules as UI) |

Example — verify steps offline:

```bash
echo '{"description":"Test","steps":[[1,40,100,15],[2,100,200,10]],
  "e720":{"sweep_mode":0,"enabled_freqs":["1000"],"range_max":10000}}' \
  | python3 src/webui/scripts/verify_temperature_steps.py
# exit 0 = OK, 1 = validation errors (JSON on stdout)
```

---

## ROS 2 integration

The `webui` node uses parameters from `config/webui.params.yaml` (installed under `share/webui/config/`).

| Interface | Default | Use |
|-----------|---------|-----|
| Service | `/core/query` | Temperature control, experiment runner |
| Service | `/database/query` | Programs, steps, E7-20 config, measurements |
| Topic | `/ltm2985/measurement` | LTM readings |
| Topic | `/measure_device` | E7-20 status |
| Topic | `/measure_device/command` | E7-20 commands |
| Topic | `/ltm2985/raw_json` | LTM stream (experiment) |
| Topic | `/ads1256` | ADS stream (experiment) |

Publish: measurement logging during program runs (when enabled).

---

## Parameters (`config/webui.params.yaml`)

| Parameter | Default | Notes |
|-----------|---------|-------|
| `bind_host` | `0.0.0.0` | HTTP bind address |
| `bind_port` | `80` | HTTP port (`CAP_NET_BIND_SERVICE` on Pi) |
| `title` | `Delatometry Control` | Header brand text |
| `auth_enabled` | `false` | HTTP Basic auth |
| `auth_user` / `auth_password` | `admin` / `admin` | Basic auth credentials |
| `status_refresh_period_sec` | `1.0` | Dashboard WS / experiment poll period |
| `control_loop_period_sec` | `1.0` | Experiment control loop |
| `enable_measurement_logging` | `true` | DB rows while program runs |
| `measurement_log_min_interval_sec` | `0.25` | Timer interval when E7-20 offline (freq/measures saved as 0) |
| `enable_service_control` | `true` | Dashboard systemctl buttons |
| `network_use_sudo` | `true` | Configuration uses `nmcli` via sudo |
| `delatometry_env_file` | `/etc/default/delatometry` | Serial ports, PWM, feature flags |
| `export_dir` | `""` | Program ZIP export directory |
| `systemd_units` | see yaml | Rows on dashboard |
| `ltm_control_channel` / `ltm_monitor_channel` | `9` / `3` | LTM channel indices |
| `stream_max_lines` | `30` | Max lines in live stream panes |

---

## Authentication

When `auth_enabled:=true` (launch param or yaml):

- Browser **HTTP Basic** auth on pages and most POSTs.
- Exempt: `/static/*`, `/ws/*`, `/api/*`, `/dashboard/snapshot`, WebSocket upgrade to `/ws/*`.

When `auth_enabled:=false` (default), no login prompt.

---

## Systemd & sudo

| Unit | Package |
|------|---------|
| `delatometry-webui.service` | This package (`run_node.sh webui`) |

Dashboard **stop/restart** on `delatometry-webui.service` and DB units is disabled in UI (use SSH).

### VPN (OpenVPN / ZeroTier)

Configuration → **Network** → **VPN** saves `/etc/delatometry/vpn.json` and optional OpenVPN profile under `/etc/delatometry/openvpn/`.

| Package | Purpose |
|---------|---------|
| `openvpn` | Client daemon for `.ovpn` profiles |
| `zerotier-one` | ZeroTier mesh (`zerotier-cli`) |

When **Connect on boot** is checked, `delatometry-vpn.service` is enabled (installed by `scripts/systemd/install_services.sh`). It runs after `network-online.target`.

Re-run after pulling VPN changes:

```bash
sudo bash src/webui/scripts/install_sudoers.sh
sudo bash scripts/systemd/install_services.sh   # installs delatometry-vpn.service
```

Install sudoers (once per machine):

```bash
sudo bash src/webui/scripts/install_sudoers.sh
```

Or manual rules for `systemctl` on `delatometry-*` and `/usr/bin/nmcli`.

---

## Core / PWM reminder

Closed-loop temperature control needs in `/etc/default/delatometry`:

```bash
DELATOMETRY_CORE_ENABLE_PWM_CONTROLLER=true
```

Dashboard shows a note when core is up but PWM is off.

---

## Install

```bash
pip install -r src/webui/requirements.txt
colcon build --packages-select database webui --symlink-install
```

Verify:

```bash
test -x install/webui/lib/webui/run.py && echo OK
python3 -c "from webui.node import main; from webui.i18n import _; print('imports OK')"
```

Optional:

```bash
sudo bash src/webui/scripts/install_sudoers.sh
```

If systemd reports `executable 'run.py' not found`, rebuild webui (stale install).

---

## Run

```bash
source /opt/ros/jazzy/setup.bash   # or your distro
source ~/ros2_delatometry/install/setup.bash
ros2 launch webui webui.launch.py
```

Or via stack install:

```bash
sudo systemctl start delatometry-webui.service
journalctl -u delatometry-webui.service -f
```

Open:

- `http://<pi-ip>/dashboard`
- `http://<pi-ip>/programs`
- Language: **EN** / **UA** in the top bar

---

## Package layout

```
webui/
  run.py              # ROS entry → node.main() → uvicorn
  node.py             # WebHMINode, business logic
  web_app.py          # FastAPI app, auth, locale, WS registration
  i18n.py             # en/uk translations
  locale/en.json, uk.json
  routes/             # dashboard, programs, experiment, config
  templates/          # Jinja2 HTML
  static/             # CSS/JS
  config/webui.params.yaml
  scripts/            # install, sudoers, verify_temperature_steps
```

---

## Related packages

| Package | Role |
|---------|------|
| [`database`](../database/) | MariaDB access via `/database/query` |
| [`core`](../core/) | Heater control via `/core/query` |
| [`measure_device`](../measure_device/) | E7-20 UART |
| [`ltm2985_uart`](../ltm2985_uart/) | LTM temperature UART |
| [`hmi`](../hmi/) | Nextion display (ROS topics only, not HTTP) |
