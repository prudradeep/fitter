# Ubuntu Backend Deployment

Use this guide to host the Dr Transition backend on an Ubuntu server. The
Windows desktop app will connect to this hosted backend URL.

This deploys only the backend API and MySQL persistence layer. Local LLM,
embeddings, RAG, and temporary evidence remain on the Windows desktop client.

## Assumptions

- Ubuntu 22.04 or 24.04 LTS
- Domain: `api.example.org`
- App directory: `/opt/dr-transition`
- Backend runs on `127.0.0.1:8000`
- Nginx terminates HTTPS
- MySQL runs on the same Ubuntu server

Replace placeholders before running commands.

## 1. Install Packages

```bash
sudo apt update
sudo apt install -y git curl build-essential mysql-server nginx
```

`uv` will install/manage Python 3.12 for the app user. This avoids relying on
Ubuntu's default Python version, which differs between 22.04 and 24.04.

## 2. Create App User and Directory

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin drtransition
sudo mkdir -p /opt/dr-transition
sudo chown drtransition:drtransition /opt/dr-transition
```

Install `uv` system-wide and let it manage Python 3.12 for the service user:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh
/usr/local/bin/uv --version
sudo -u drtransition -H bash -lc '/usr/local/bin/uv python install 3.12'
```

Confirm system packages:

```bash
mysql --version
nginx -v
```

Clone or copy the backend release:

```bash
sudo -u drtransition git clone <your-repo-url> /opt/dr-transition
cd /opt/dr-transition
sudo -u drtransition -H bash -lc 'cd /opt/dr-transition && /usr/local/bin/uv python pin 3.12'
sudo -u drtransition -H bash -lc 'cd /opt/dr-transition && /usr/local/bin/uv sync'
```

If you deploy with archives instead of Git, unpack the release into
`/opt/dr-transition` and keep ownership as `drtransition:drtransition`.

## 3. Configure MySQL

Secure MySQL first:

```bash
sudo mysql_secure_installation
```

Create the database and user:

```bash
sudo mysql
```

```sql
CREATE DATABASE drtransition CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'drtransition'@'localhost' IDENTIFIED BY '<strong-db-password>';
GRANT ALL PRIVILEGES ON drtransition.* TO 'drtransition'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

## 4. Create Backend `.env`

Create `/opt/dr-transition/.env`:

```bash
sudo -u drtransition nano /opt/dr-transition/.env
```

Example:

```env
APP_NAME="Dr Transition"
APP_ENV=production
APP_DEBUG=false
SECRET_KEY="<strong-random-secret>"

DATABASE_URL="mysql+pymysql://drtransition:<strong-db-password>@127.0.0.1:3306/drtransition"
DATABASE_AUTO_MIGRATE=false

AUTH_COOKIE_SECURE=true
CSRF_PROTECTION_ENABLED=true
CORS_ORIGINS="https://api.example.org,tauri://localhost,http://tauri.localhost"

STRUCTURED_LOGS=true
ACCESS_LOG_ENABLED=true
ACCESS_LOG_SUPPRESSED_PATHS="/health,/health/live,/health/ready"

LLM_LOG_ENABLED=false
LLM_LOG_TO_FILE=false
LLM_LOG_TO_DB=false
LLM_LOG_INCLUDE_PAYLOADS=false
```

If the database password contains URL-reserved characters, encode them in
`DATABASE_URL`. For example, `@` becomes `%40`, `#` becomes `%23`, `/` becomes
`%2F`, and `:` becomes `%3A`.

Example:

```env
DATABASE_URL="mysql+pymysql://drtransition:Anyth!ng%401301@127.0.0.1:3306/drtransition"
```

Generate a strong secret:

```bash
openssl rand -hex 32
```

Protect the file:

```bash
sudo chown drtransition:drtransition /opt/dr-transition/.env
sudo chmod 600 /opt/dr-transition/.env
```

## 5. Apply Schema, Migrations, and Seeds

```bash
cd /opt/dr-transition
sudo -u drtransition -H bash -lc 'cd /opt/dr-transition && PYTHONPATH=/opt/dr-transition /usr/local/bin/uv run python scripts/apply_migrations.py --apply-base-schema'
sudo -u drtransition -H bash -lc 'cd /opt/dr-transition && PYTHONPATH=/opt/dr-transition /usr/local/bin/uv run python scripts/apply_migrations.py'
sudo -u drtransition -H bash -lc 'cd /opt/dr-transition && PYTHONPATH=/opt/dr-transition /usr/local/bin/uv run python -m app.seed_data'
```

For future releases, usually run only:

```bash
sudo -u drtransition -H bash -lc 'cd /opt/dr-transition && PYTHONPATH=/opt/dr-transition /usr/local/bin/uv run python scripts/apply_migrations.py'
```

Run the seed command again only when reference data changes.

If you see `ModuleNotFoundError: No module named 'app'`, rerun the command with
`PYTHONPATH=/opt/dr-transition` as shown above. This is required because the
repository is not installed as a Python package during deployment.

## 6. Create systemd Service

Create `/etc/systemd/system/dr-transition.service`:

```bash
sudo nano /etc/systemd/system/dr-transition.service
```

```ini
[Unit]
Description=Dr Transition hosted backend
After=network.target mysql.service

[Service]
Type=simple
User=drtransition
Group=drtransition
WorkingDirectory=/opt/dr-transition
EnvironmentFile=/opt/dr-transition/.env
ExecStart=/usr/local/bin/uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips="127.0.0.1"
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

If `uv` is installed somewhere else, check with:

```bash
/usr/local/bin/uv --version
```

Then update `ExecStart`.

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dr-transition
sudo systemctl status dr-transition
```

Logs:

```bash
journalctl -u dr-transition -f
```

## 7. Configure Nginx

Create `/etc/nginx/sites-available/dr-transition`:

```bash
sudo nano /etc/nginx/sites-available/dr-transition
```

```nginx
server {
    listen 80;
    server_name api.example.org;

    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

If you are testing by public IP before DNS is ready, make this the default
server block and include the IP:

```nginx
server {
    listen 80 default_server;
    server_name 13.232.131.219 _;

    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable it:

```bash
sudo ln -s /etc/nginx/sites-available/dr-transition /etc/nginx/sites-enabled/dr-transition
sudo nginx -t
sudo systemctl reload nginx
```

## 8. Add HTTPS

With Certbot:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d api.example.org
```

After TLS is active:

```bash
curl -fsS https://api.example.org/health/live
curl -fsS https://api.example.org/health/ready
```

## 9. Firewall

Allow SSH and HTTPS:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status
```

Do not expose MySQL publicly unless you have a separate secured database
network.

## 10. Configure Windows Desktop Installer

When building or installing the Windows desktop app, use:

```text
DR_TRANSITION_BACKEND_URL=https://api.example.org/
DR_TRANSITION_BACKEND_HEALTH_URL=https://api.example.org/health/ready
DR_TRANSITION_BACKEND_AUTH_CHECK_URL=https://api.example.org/api/sessions
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=mistral-nemo
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
```

The installer writes these no-secret values to:

```text
%ProgramData%\DrTransition\.env
```

## 11. Smoke Test

On Ubuntu:

```bash
curl -i https://api.example.org/health/live
curl -i https://api.example.org/health/ready
```

When testing by public IP before HTTPS/DNS:

```bash
curl -i http://13.232.131.219/health/live
curl -i http://13.232.131.219/health/ready
curl -i http://13.232.131.219/login
```

From a browser:

1. Open `https://api.example.org/login`.
2. Log in.
3. Confirm `/api/sessions` returns authenticated data.

From the Windows desktop app:

1. Launch the app.
2. Confirm hosted backend diagnostics pass.
3. Confirm local Ollama diagnostics pass.
4. Login.
5. Confirm KB sync starts.
6. Upload temporary evidence locally.
7. Validate locally.
8. Promote accepted evidence.
9. Confirm promoted evidence appears in `validated_evidence` sync.

## 12. Release Update Procedure

```bash
cd /opt/dr-transition
sudo systemctl stop dr-transition
sudo -u drtransition -H bash -lc 'cd /opt/dr-transition && git pull'
sudo -u drtransition -H bash -lc 'cd /opt/dr-transition && /usr/local/bin/uv sync'
sudo -u drtransition -H bash -lc 'cd /opt/dr-transition && PYTHONPATH=/opt/dr-transition /usr/local/bin/uv run python scripts/apply_migrations.py'
sudo systemctl start dr-transition
sudo systemctl status dr-transition
curl -fsS https://api.example.org/health/ready
```

Run seeds only when reference data changed:

```bash
sudo -u drtransition -H bash -lc 'cd /opt/dr-transition && PYTHONPATH=/opt/dr-transition /usr/local/bin/uv run python -m app.seed_data'
```

## 13. Useful Checks

```bash
sudo systemctl status dr-transition
journalctl -u dr-transition -n 100 --no-pager
sudo nginx -t
sudo tail -n 100 /var/log/nginx/error.log
sudo mysql -e "SELECT COUNT(*) FROM drtransition.app_users;"
```

## Important Boundaries

- Do not install Ollama on the Ubuntu backend for normal workflow paths.
- Do not store temporary evidence on the backend.
- Do not run migrations or seeds from the Windows installer.
- Do not bundle backend files into the Windows installer.
- The backend stores only promoted `validated_evidence`.
