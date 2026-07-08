# Server Runners

These examples run the central FastAPI server with `APP_MODE=cloud_server`.
The central server is the only component that should know the cloud MySQL
`DATABASE_URL`.

## Local Production Smoke Test

```powershell
$env:APP_MODE = "cloud_server"
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/health` on the server host.

## Windows Service

Use a service runner such as NSSM or Windows Service Control to run:

```powershell
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Recommended service settings:

- Working directory: repository or installed application directory.
- Environment file: `%PROGRAMDATA%\DrTransition\.env` for packaged installs.
- Startup type: automatic.
- Restart on failure: enabled.
- Log output: `data/service-runtime/logs`.

## Linux systemd

Create `/etc/systemd/system/dr-transition.service`:

```ini
[Unit]
Description=Dr Transition Central API
After=network-online.target

[Service]
WorkingDirectory=/opt/dr-transition
EnvironmentFile=/opt/dr-transition/.env
ExecStart=/usr/bin/uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
User=drtransition
Group=drtransition

[Install]
WantedBy=multi-user.target
```

Then run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dr-transition
sudo systemctl status dr-transition
```

## Reverse Proxy Shape

Put Nginx, IIS, Apache, or a managed load balancer in front of the app:

```text
Internet HTTPS :443
  -> reverse proxy with TLS certificate
  -> http://127.0.0.1:8000
  -> FastAPI app
  -> cloud MySQL
```

The proxy must pass `Authorization` headers because sync and evidence endpoints
use bearer tokens.

## Health Checks

- `GET /health` for process health.
- `GET /api/sync/status` after login for client-visible sync status.
- Review application logs for startup sync failures and database connection errors.
