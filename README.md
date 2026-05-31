# webui

FastAPI browser HMI (port 80): dashboard, programs, experiment, configuration, **documentation browser** at `/docs`.

**Full documentation:** [docs/en/webui.md](../../docs/en/webui.md) · [docs/uk/webui.md](../../docs/uk/webui.md)

```bash
pip install -r src/webui/requirements.txt
colcon build --packages-select webui
ros2 launch webui webui.launch.py
```

Service: `delatometry-webui.service`. Nextion display is package **`hmi`**, not this package.
