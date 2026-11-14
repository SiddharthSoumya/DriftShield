# Server operations

## Prepare the server

Use a systemd Linux host with a patched Python 3.12+, venv, PostgreSQL 16+, and an HTTPS reverse proxy. Create a dedicated PostgreSQL login and database owned by that login. The application requires schema migration privileges in its database. It does not need a database superuser. Configure backups and network access before connecting customer repositories.

The installer uses `/opt/driftshield/releases`, the `/opt/driftshield/current` symlink, `/etc/driftshield/service.env`, and `/usr/local/sbin/driftshieldctl`. It creates a non-login service account and installs the two systemd units. The config is a root-managed shell file. Quote values containing spaces or shell metacharacters. Set `DS_DB_SSLMODE=verify-full` for a remote database and install its CA certificate. Local deployment can use PostgreSQL over trusted loopback. Set `DS_ORIGIN` to the external HTTPS origin without a path.

Run `sudo bash deploy/install.sh` to create the configuration. Edit it, then rerun. Place a reverse proxy in front of loopback port 8000. It must overwrite forwarded protocol headers. The supplied example requires Caddy 2.10+ for request body limits. Copy it, replace the domain, and validate it with `caddy validate --config /etc/caddy/Caddyfile` before reloading. For a directly internet-facing loopback proxy that overwrites `X-DriftShield-Client-IP`, set `DS_TRUST_PROXY_CLIENT_IP=1`. For a proxy chain, configure your proxy to set that header from its verified client address or keep the setting disabled. Keep port 8000 closed externally. Use a proxy-level per-client login limit for busy or public deployments. With the trusted client-IP option disabled, the built-in IP counter uses the immediate network peer, which is normally the proxy.

SMTP is optional for evaluation and needed for email notifications. Set `DS_SMTP_HOST`, `DS_SMTP_PORT`, `DS_SMTP_USER`, `DS_SMTP_PASSWORD`, and `DS_FROM_EMAIL`. STARTTLS is enabled by default. Restart both services after changing environment values. Set a workspace recipient and send a test email from Settings. “Accepted by SMTP” means the relay accepted the message. Confirm mailbox delivery separately.

## Routine commands

```bash
sudo driftshieldctl provision engineering owner --name "Engineering"
sudo driftshieldctl provision engineering reviewer --role operator
sudo driftshieldctl provision engineering auditor --role viewer
sudo driftshieldctl revoke engineering reviewer
sudo driftshieldctl changepassword owner
sudo systemctl status driftshield-web driftshield-worker
sudo journalctl -u driftshield-web -u driftshield-worker -n 100
curl -fsS http://127.0.0.1:8000/healthz
```

The owner-only worker health endpoint is available at `/w/WORKSPACE/worker.json`. Use systemd or an independent uptime check for the worker because a stopped worker cannot send its own overdue alert. Workspace scan failures appear on the repository page. Repeated rate-limit failures indicate that you need fewer anonymous scans or an authenticated GitHub App. Monitor PostgreSQL disk usage and outbox failure status.

## Upgrade and recover

1. Back up PostgreSQL and securely store `/etc/driftshield/service.env` separately. Recovery requires the database and the GitHub encryption key. Test restore into an isolated database before relying on a backup.
2. Fetch and review the new release. Run the installer. It builds a new release directory and runs preflight checks before stopping current services. It applies migrations while both services are stopped, switches the current symlink, and starts the new units.
3. Confirm both units are active, `/healthz` returns 200, the worker heartbeat advances, and an owner can sign in.
4. If migration fails, both services can remain stopped. Read the migration output. Inspect schema state and restore the backup before starting an older release if schema compatibility is uncertain. The installer does not reverse schema changes automatically.
5. Repoint `/opt/driftshield/current` to the previous release only when its schema is compatible or after restoring the matching database backup. Reload systemd if unit definitions changed and restart both services.

Back up with your PostgreSQL provider's tools or `pg_dump -Fc`. Restore into a fresh database using `pg_restore` with the appropriate database owner. A retention policy controls active tables. Set independent expiration for backup copies. Stop worker and web before restoring in place.

## Acceptance procedure

On a staging host, verify TLS and both systemd services. Provision two workspaces with separate users and confirm each can see only its own evidence. Connect a disposable GitHub App to a sandbox repository. Import it, collect its first observation, and approve all settings. Change a supported setting or an active ruleset, wait for a scan, verify the finding and SMTP email, acknowledge it, restore the setting, and verify that the next successful scan closes the incident. Repeat after removing installation access to verify unavailable collection preserves the last successful observation. Export evidence. Test restart and database restore before using customer data.

## Infrastructure staging

After the web and worker services are active, follow the [collector guide](infrastructure.md) for each supported environment. The server installer includes the ingestion code. Install the relevant provider CLI on the separate collector runner. Configure the HTTPS proxy to accept observations up to 1 MB. Verify baseline approval, a supported change, a failed collection, a restoration and evidence export against a sandbox scope before a customer rollout.

## Audit release changes

Use `/livez` for web-process liveness and `/healthz` for database readiness. The owner-only worker route now reports the latest completed tick for email, GitHub collection, freshness checks, and retention. A completed tick indicates the stage ran. Confirm actual delivery and collection success using the corresponding records and external probes.

Remote production PostgreSQL requires `DS_DB_SSLMODE=verify-full`. Set `DS_DB_SSLROOTCERT` to the server's CA certificate file readable by the service account if required. Use a verified STARTTLS or implicit TLS SMTP relay. Set `DS_SMTP_SSL=1` for implicit TLS. A loopback SMTP relay without credentials may use plaintext locally. See the [incident runbook](incident-response.md) for outage and recovery procedures.
