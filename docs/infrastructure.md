# Infrastructure drift

Create an infrastructure target from the workspace dashboard. Pick a stable scope and expected scan interval. Save the one-time token and HTTPS endpoint on a trusted runner. Run the matching collector, inspect the first successful observation, and approve it. The runner is the source of observations and should be controlled by the customer.

| Collector | Scope | Coverage |
| --- | --- | --- |
| Terraform / OpenTofu | One initialized workspace and its existing state backend | Managed resource refresh drift from plan JSON, projected to resource type, opaque address ID and action. Configuration changes must be reviewed in the original plan. |
| Docker | One explicit Docker context and fixed set of named containers | Image ID, memory, CPU, privileged mode, read-only root, restart policy, network mode class, and digests for published ports and added capabilities |
| Kubernetes | One context and namespace | Deployments, StatefulSets, DaemonSets, Services and HPAs. Includes replica count, image and resource digests, privileged and host networking flags, service type/ports, and HPA min/max. |
| AWS | One account and region | EC2 instance types and states, EBS size/type/IOPS/throughput, NAT gateways, RDS instance class/storage/Multi-AZ, ASG capacities, ELB type/scheme/AZ count, and Elastic IP associations |
| Custom snapshot | One stable inventory scope | Any allowlisted scalar JSON resource map produced by a customer-owned collector. Can bridge Azure, GCP, or internal systems via custom code. |

The AWS and Kubernetes findings flag selected cost or operational drivers. They are configuration evidence. No price estimates, usage charges, invoice reconciliation, or comprehensive cloud inventory are calculated. Dynamic fleets and autoscaling can legitimately change instance inventory, running state, replica counts, or desired capacity. Use a stable pilot scope and review expected changes before subscribing a team to alerts.

## Install a collector

Check out the same DriftShield release on a CI runner or host with Python 3.12+. The `collector` package uses the Python standard library. The runner needs the CLI for the selected platform, network access to that platform, and outbound HTTPS to DriftShield. It holds its own platform credentials. Store the endpoint and target token in your runner secret store or an owner-readable environment file. Never put the token in Git.

From the repository root:

```bash
python3 -m collector docker --scope billing-host --context production --container billing-api --container billing-worker
python3 -m collector kubernetes --scope prod:payments --context prod --namespace payments
python3 -m collector aws --scope 123456789012:us-east-1 --region us-east-1
python3 -m collector snapshot --scope internal-platform --file snapshot.json
```

The examples assume `DRIFTSHIELD_ENDPOINT` and `DRIFTSHIELD_TOKEN` are configured. Add `--preview` for a local preview. A failed command reports a structured failure and exits nonzero. The last successful observation and open findings remain visible. For immutable and dynamic environments, give each target an explicit owner and keep collector arguments stable. A scope is a promise of inventory completeness. Changing the selected containers or account coverage midstream can look like removals and should be handled as a new target or a deliberate baseline change.

For Docker use a trusted host/context and named containers. Docker daemon access can confer broad host authority even though the commands used here only list and inspect. Keep that access with the runner and away from the DriftShield server. For Kubernetes grant namespace-scoped list access to the listed resources in their API groups. The collector does not query Secrets. For AWS use AWS CLI v2 with a customer-owned role permitting `ec2:DescribeInstances`, `ec2:DescribeVolumes`, `ec2:DescribeNatGateways`, `ec2:DescribeAddresses`, `rds:DescribeDBInstances`, `autoscaling:DescribeAutoScalingGroups` and `elasticloadbalancing:DescribeLoadBalancers`. The collector checks its AWS account identity before reading the region. Review the IAM restrictions with the account owner.

### Terraform / OpenTofu CI step

Run the plan in the existing trusted workspace, with the same workspace and backend used for normal operations. Initialize it before the step. Use a plan format of 1.1 or newer, and avoid targeted or partial plans. Use `tofu` for OpenTofu. Set `IAC_CLI` only to the intended CLI.

```bash
set -euo pipefail
umask 077
project="${DRIFTSHIELD_CHECKOUT:?Set the path to the DriftShield checkout}"
iac_cli="${IAC_CLI:-terraform}"
scope="production-platform"
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
run_collector() {
  PYTHONPATH="$project" python3 -m collector terraform --scope "$scope" "$@"
}
if "$iac_cli" plan -refresh-only -input=false -out="$work/plan" >"$work/plan.log" 2>&1 &&
   "$iac_cli" show -json "$work/plan" >"$work/plan.json"; then
  run_collector --file "$work/plan.json"
else
  run_collector --collection-failed
fi
```

The plan and state can contain sensitive values. Keep them in the private temp directory, avoid printing or archiving them, and use the local plan to identify affected resource addresses. DriftShield receives a sanitized drift summary. The existing IaC state supplies the source of truth for refresh-only drift. An empty baseline can be approved after a clean observation. Drift is shown immediately, including before a baseline exists. A refresh-only plan describes state changes. Review an ordinary plan in your IaC workflow to decide how to change live infrastructure. [Terraform plan reference](https://developer.hashicorp.com/terraform/cli/commands/plan) · [Sensitive JSON output](https://developer.hashicorp.com/terraform/cli/commands/show)

## Schedule and verify remediation

Use the customer's CI scheduler, cron, or a systemd timer. The target's expected interval configures overdue status. It does not launch a remote command. For example, for a 30-minute Docker schedule, place the following command in the runner's trusted schedule with a secret environment and an absolute checkout path:

```bash
cd /opt/driftshield-collector
python3 -m collector docker --scope billing-host --context production --container billing-api
```

When a finding opens, review the approved and observed values, record the change ticket, and restore through the normal Terraform, GitOps, Compose, or AWS change process. Run the collector again. A matching observation closes the finding as verified after a review. An intentional difference can be accepted as a new baseline for snapshot targets. Terraform drift is reconciled through its existing workflow.

Production staging should exercise one target per provider you intend to sell. Test a supported change, a restoration, an upstream permission error, a missed collection, a repeated submission, and SMTP delivery. Never change a customer production environment merely to test the monitor.

## Limits and deployment boundary

Each observation supports at most 500 resources, 32 fields per resource and a 1 MB request. Configure the reverse proxy to permit that size, preserve the request length, and rate-limit the ingestion route. Use an AWS account/region whose supported inventory fits the limits. Partial, malformed, or expired deliveries are rejected. Snapshots are allowlisted but may contain infrastructure names or operational metadata. Review this information before sending it to a hosted service. A target token authorizes observations for its own target and its holder can submit observations. Rotate the token if its runner is compromised.

The dashboard and intake run on the same DriftShield server. The collector runs separately and needs no inbound connection. The project does not execute the remediation or accept remote commands through the collector.
