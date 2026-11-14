#!/bin/bash
set -euo pipefail
umask 022
if [[ $EUID != 0 ]]; then
  echo "Run with sudo on a systemd Linux server." >&2
  exit 1
fi
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${DRIFTSHIELD_PYTHON:-python3}"
"$python_bin" -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12 or newer is required"'
command -v systemctl >/dev/null
command -v runuser >/dev/null
command -v flock >/dev/null
getent group driftshield >/dev/null || groupadd --system driftshield
id driftshield >/dev/null 2>&1 || useradd --system --gid driftshield --home-dir /var/lib/driftshield --shell /usr/sbin/nologin driftshield
install -d -m 0750 -o root -g driftshield /etc/driftshield
install -d -m 0755 /opt/driftshield/releases
exec 9>/opt/driftshield/.install.lock
flock -n 9
env_file=/etc/driftshield/service.env
if [[ ! -f "$env_file" ]]; then
  secret="$("$python_bin" -c 'import secrets; print(secrets.token_urlsafe(64))')"
  encryption="$("$python_bin" -c 'import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')"
  install -m 0640 -o root -g driftshield /dev/null "$env_file"
  cat > "$env_file" <<CONFIG
DS_SECRET_KEY=$secret
DS_GITHUB_ENCRYPTION_KEY=$encryption
DS_ORIGIN=https://driftshield.example.com
DS_DB_NAME=driftshield
DS_DB_USER=driftshield
DS_DB_PASSWORD=
DS_DB_HOST=127.0.0.1
DS_DB_PORT=5432
# DS_DB_SSLMODE=verify-full
# DS_DB_SSLROOTCERT=/etc/driftshield/db-ca.pem
# DS_TRUST_PROXY_CLIENT_IP=1
# DS_SMTP_HOST=smtp.example.com
# DS_SMTP_PORT=587
# DS_SMTP_USER=
# DS_SMTP_PASSWORD=
# DS_FROM_EMAIL=driftshield@example.com
CONFIG
  echo "Created $env_file. Configure your HTTPS origin, PostgreSQL credentials, and optional SMTP. Then rerun."
  exit 0
fi
chown root:driftshield "$env_file"
chmod 0640 "$env_file"
set -a
source "$env_file"
set +a
if [[ "${DS_ORIGIN:-}" == "https://driftshield.example.com" || "${DS_DEV:-}" == 1 ]]; then
  echo "Configure the real DS_ORIGIN and remove DS_DEV before installing." >&2
  exit 1
fi
release="$(mktemp -d /opt/driftshield/releases/release.XXXXXXXX)"
chmod 755 "$release"
for item in manage.py requirements.txt driftshield governance infrastructure collector templates static; do
  cp -R -- "$source_dir/$item" "$release/"
done
install -m 0755 "$source_dir/deploy/service-launch" "$release/service-launch"
find "$release" -name '__pycache__' -type d -prune -exec rm -r -- {} +
"$python_bin" -m venv "$release/.venv"
"$release/.venv/bin/python" -m pip install --disable-pip-version-check --only-binary=:all: -r "$release/requirements.txt"
cd "$release"
.venv/bin/python manage.py check --deploy --fail-level WARNING
.venv/bin/python manage.py collectstatic --no-input
install -m 0644 "$source_dir/deploy/driftshield-web.service" /etc/systemd/system/driftshield-web.service
install -m 0644 "$source_dir/deploy/driftshield-worker.service" /etc/systemd/system/driftshield-worker.service
install -m 0755 "$source_dir/deploy/driftshieldctl" /usr/local/sbin/driftshieldctl
if [[ -L /opt/driftshield/current ]]; then
  systemctl stop driftshield-web.service driftshield-worker.service
fi
runuser --preserve-environment -u driftshield -- .venv/bin/python manage.py migrate --no-input
ln -sfn "$release" /opt/driftshield/current.new
mv -Tf /opt/driftshield/current.new /opt/driftshield/current
systemctl daemon-reload
systemctl enable --now driftshield-web.service driftshield-worker.service
systemctl is-active driftshield-web.service driftshield-worker.service
