# DriftShield v2

DriftShield compares observed GitHub repository settings with an explicitly approved baseline. It shows the values that changed, groups repeat observations into incidents, and records when a setting returns to baseline or an operator approves a new baseline.

This release is a self-hosted, single-operator pilot. It monitors repository visibility, default branch, archived status, issues, and wiki settings. The GitHub integration is read-only.

## Run locally

From the repository root, with Python 3.12 or newer:

```bash
cd v2
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m driftshield
```

Open <http://127.0.0.1:8000>. Add a public repository such as `SiddharthSoumya/DriftShield`, select **Scan now**, inspect the values, and approve the baseline. Use a separate repository you own to try a settings change. Changing an enabled wiki flag is enough to demonstrate detection. Scan again, acknowledge the incident, restore the original value in GitHub, and scan once more to see the incident resolve.

## Private repositories and visibility changes

Use the GitHub Actions collector for a private repository or for a repository whose visibility may change. A direct public scan loses access if a repository becomes private. DriftShield reports that as a failed scan, since the same failure can also mean a rename, transfer, or deletion.

1. Run DriftShield on an HTTPS address reachable by the GitHub Actions runner.
2. Add the repository using the **GitHub Actions runner** collector.
3. Copy [the example workflow](examples/driftshield.yml) to `.github/workflows/driftshield.yml` on the monitored repository's default branch.
4. Create the GitHub Actions secret `DRIFTSHIELD_TOKEN` with the monitor's one-time token and the repository variable `DRIFTSHIELD_INGEST_URL` with the full HTTPS ingest URL shown in the dashboard.
5. Run the workflow manually, then review and approve the first observation.

The workflow uses GitHub's job token to read settings and sends only the repository identifier and the monitored fields. It runs twice an hour by default. Private workflows consume the repository owner's included runner usage and can incur charges beyond it. Change the schedule for your budget. A delayed or missed workflow appears as an overdue observation. Rotate the monitor token from the dashboard to revoke the old credential.

## Self-hosting

The application defaults to loopback, one process, and `data/driftshield.db`. Keep a single instance per database. The SQLite file contains repository settings, incident history, and hashed runner tokens.

Build and run with a persistent volume. Set a unique `DRIFTSHIELD_PASSWORD` of at least 16 characters in your shell first. The operator username is `admin`.

```bash
docker build -t driftshield:v2 v2
docker run --rm --name driftshield -p 127.0.0.1:8000:8000 \
  --env DRIFTSHIELD_PASSWORD -v driftshield-data:/data driftshield:v2
```

Terminate HTTPS at a reverse proxy before exposing the dashboard or ingest endpoint. Rate-limit inbound requests at the proxy. Configure health checks against `/health`. Set `DRIFTSHIELD_SCAN_SECONDS` to a value of at least 300 to override the default 900-second public scan cadence, or to 0 for manual scans. GitHub rate limits apply to the public collector. Back up SQLite with its online backup API or the `.backup` command and test recovery before depending on the data. History is stored locally, and the UI displays recent records.

## Verify

```bash
cd v2
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

The integration tests use temporary databases and controlled GitHub observations. See [the repository audit and product roadmap](PRODUCT.md) for the hosted release design.
