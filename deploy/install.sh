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
# DS_PILOT_EBS=1
# DS_PILOT_WORKSPACE=
# DS_PILOT_PROBE_TOKEN=
DS_PILOT_RECOVERY_FENCED=1
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
reviewed_sha="${DRIFTSHIELD_RELEASE_SHA:-}"
if command -v git >/dev/null && git -C "$source_dir" rev-parse --verify HEAD >/dev/null 2>&1; then
  actual_sha="$(git -C "$source_dir" rev-parse --verify HEAD)"
  if git -C "$source_dir" diff --quiet HEAD -- && [[ -z "$(git -C "$source_dir" ls-files --others --exclude-standard)" ]]; then
    if [[ -n "$reviewed_sha" && "$reviewed_sha" != "$actual_sha" ]]; then
      echo "Requested release SHA does not match the checkout." >&2
      exit 1
    fi
    reviewed_sha="$actual_sha"
  elif [[ "${DS_PILOT_EBS:-0}" == 1 ]]; then
    echo "Use a clean, reviewed pilot checkout." >&2
    exit 1
  fi
fi
if [[ -n "$reviewed_sha" && ! "$reviewed_sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "DRIFTSHIELD_RELEASE_SHA must be a full Git commit SHA." >&2
  exit 1
fi
if [[ "${DS_PILOT_EBS:-0}" == 1 && ( -z "$reviewed_sha" || "${DS_PILOT_RECOVERY_FENCED:-0}" != 1 ) ]]; then
  echo "Install the reviewed pilot release with DS_PILOT_RECOVERY_FENCED=1." >&2
  exit 1
fi
export DS_RELEASE_SHA="$reviewed_sha"
previous="$(readlink -f /opt/driftshield/current || true)"
release="$(mktemp -d /opt/driftshield/releases/release.XXXXXXXX)"
chmod 755 "$release"
printf '%s\n' "$reviewed_sha" > "$release/release.sha"
printf '%s\n' "$previous" > "$release/previous-release"
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
if ! runuser --preserve-environment -u driftshield -- .venv/bin/python manage.py migrate --no-input; then
  echo "Migration failed. Services remain stopped. Previous release: $previous" >&2
  exit 1
fi
if [[ "${DS_PILOT_EBS:-0}" == 1 ]]; then
  runuser --preserve-environment -u driftshield -- .venv/bin/python manage.py pilot_check --workspace "$DS_PILOT_WORKSPACE" --bootstrap
fi
ln -sfn "$release" /opt/driftshield/current.new
mv -Tf /opt/driftshield/current.new /opt/driftshield/current
systemctl daemon-reload
if [[ "${DS_PILOT_EBS:-0}" == 1 ]]; then
  systemctl disable --now driftshield-worker.service
  systemctl enable --now driftshield-web.service
  systemctl is-active driftshield-web.service
  echo "Pilot fenced. Complete docs/pilot-acceptance.md before recording production activation."
else
  systemctl enable --now driftshield-web.service driftshield-worker.service
  systemctl is-active driftshield-web.service driftshield-worker.service
fi
