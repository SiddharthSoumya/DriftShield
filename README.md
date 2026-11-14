# DriftShield

**Infrastructure and GitHub configuration drift monitoring with baseline approval and remediation verification.**

DriftShield compares approved configuration with observations from Terraform/OpenTofu, Docker, Kubernetes, AWS and GitHub. It flags selected cost-driving or security-sensitive changes, routes findings by email, and verifies remediation from subsequent observations. Deploy it with a PostgreSQL-backed dashboard and worker. Run infrastructure collectors inside the customer's environment.

- **Infrastructure:** Terraform/OpenTofu refresh summaries, Docker runtime configuration, Kubernetes workloads and scaling, AWS EC2, EBS, NAT, RDS, Auto Scaling, ELB and Elastic IPs, plus a custom snapshot protocol.
- **GitHub:** Repository settings and active default-branch rulesets through read-only access.
- **Review:** Field-level diffs, a findings queue with assignment, expiring exceptions, runbook links, post-review verification, and CSV evidence.
- **Operations:** Workspace roles, SMTP alerts, retention, health checks and systemd install/upgrade.

Review the [coverage and collector guide](docs/infrastructure.md) before onboarding. Infrastructure remediation is applied through the team's existing tools and then verified by DriftShield.

## Release status

The current code is on `main`, including the infrastructure expansion and the [pre-release hardening](https://github.com/SiddharthSoumya/DriftShield/pull/5). It is ready for staging. A dedicated, customer-hosted pilot is conditional on the [deployment acceptance gates](docs/release-readiness.md#customer-hosted-release-gates). Public shared hosting remains outside the approved release scope.

## Try it locally

With Python 3.12 or newer:

```bash
git clone --branch main https://github.com/SiddharthSoumya/DriftShield.git
cd DriftShield
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export DS_DEV=1
python manage.py migrate
python manage.py provision demo owner --name "Demo workspace"
python manage.py runserver 127.0.0.1:8000
```

In a second terminal, run `DS_DEV=1 .venv/bin/python manage.py work`. Open [the local dashboard](http://127.0.0.1:8000), sign in, and add a public repository you control. Once the worker has collected it, refresh the repository page and approve the first baseline. Local mode uses SQLite. Create an Infrastructure target to connect a customer-run collector using the [collector guide](docs/infrastructure.md).

## Install on a Linux server

You need systemd, Python 3.12+ with venv, PostgreSQL 16+, and an HTTPS origin served by a reverse proxy such as Caddy. Run the installer from a reviewed checkout:

```bash
sudo bash deploy/install.sh
sudoedit /etc/driftshield/service.env
sudo bash deploy/install.sh
sudo driftshieldctl provision engineering owner --name "Engineering"
```

The first invocation creates the configuration file. Set your HTTPS URL and database credentials before running it again. The services bind to loopback and come up on boot. Configure your HTTPS proxy with [the example](deploy/Caddyfile). See [installation, backups, and upgrades](docs/operations.md) before putting customer data on the server.

For private repositories, register a dedicated GitHub App with repository Metadata read access, install it on selected repositories, and run `sudo driftshieldctl connectgithub` as described in [GitHub setup](docs/github.md). Workspace owners can then import those repositories from the dashboard.

## Verify and operate

```bash
DS_DEV=1 python manage.py test
DS_DEV=1 python manage.py makemigrations --check --dry-run
for script in deploy/install.sh deploy/service-launch deploy/driftshieldctl; do bash -n "$script"; done
sudo systemctl status driftshield-web driftshield-worker
```

CI covers PostgreSQL regression tests, systemd installation and upgrade, ingestion, test-database outage and recovery, backup restoration, and live smoke tests for Terraform, OpenTofu, Docker and Kubernetes. The [deployment acceptance procedure](docs/operations.md#acceptance-procedure) separately calls for a real GitHub App scan, AWS sandbox collection for AWS deployments, SMTP mailbox delivery, external worker monitoring, and clean-host recovery.

## Documentation

| Document | Contents |
| --- | --- |
| [Product](docs/product.md) | Current scope, positioning, buyers and roadmap |
| [Findings workflow](docs/drift-operations.md) | Triage, exceptions, remediation review and timeline exports |
| [Infrastructure](docs/infrastructure.md) | Coverage, permissions, scheduling and remediation workflow |
| [Commercial](docs/commercial.md) | Customer-hosted and managed deployment models |
| [Architecture](docs/architecture.md) | Module boundaries, worker behavior, consistency and failure handling |
| [Database](docs/database.md) | Tables, constraints, retention and migration strategy |
| [Web and API](docs/api.md) | Routes, payloads, permissions and response behavior |
| [Frontend](docs/frontend.md) | Page map, states, accessibility and workflow design |
| [GitHub](docs/github.md) | App setup, permissions, coverage and collection limits |
| [Operations](docs/operations.md) | Install, upgrade, health checks, troubleshooting and recovery |
| [Pre-release audit](docs/pre-release-audit.md) | Security, functional, operations, chaos, and compliance assessment |
| [Incident response](docs/incident-response.md) | Containment, service recovery, and verification |
| [Release readiness](docs/release-readiness.md) | Customer-hosted acceptance gates and remaining risks |
| [Security](docs/security.md) | Trust model, security controls, and operator responsibilities |

[MIT license](LICENSE). The original prototype is in the [v1 commit](https://github.com/SiddharthSoumya/DriftShield/tree/dfdf88f8fe16318f110839bb3f98db75d0e2cbbe). The [v2 branch](https://github.com/SiddharthSoumya/DriftShield/tree/driftshield-v2) contains the earlier single-operator pilot.

## Dedicated AWS EBS pilot

Follow the [pilot acceptance and rollback runbook](docs/pilot-acceptance.md) for the gated, read-only EBS deployment. The pilot requires a private customer-managed access path, two named owners, independent mailbox monitoring and a measured recovery drill.
