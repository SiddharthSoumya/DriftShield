# Frontend design

The workspace overview answers four questions at a glance: how many repositories are tracked, how many match baseline, how many require attention, and how many findings are open. The repository table shows collector type, scan freshness, and open findings. It links directly to the evidence behind a status.

The repository page puts the approved and observed values beside each other. A reviewer checks only the fields to approve and supplies a reason. The page shows the last successful scan, next attempt, findings and baseline history. Older rule snapshots are available under each version. Error messages indicate when the visible values came from a previous successful scan.

The Infrastructure view lists targets, platform coverage, last successful observations and open findings. A target page shows the baseline and observed values, supports review notes and baseline approval, and links to CSV evidence. Owners can view a credential expiry, rotate or revoke access, and inspect the observed source ID. Newly issued tokens are displayed once.

Workspace Activity lists operator actions. Workspace Settings covers policy, SMTP route, digest, retention, installation status and membership. Workspace provisioning and membership changes use the server CLI.

Templates use semantic landmarks, named fields, visible keyboard focus, text with status colors, and mobile layouts with scrollable tables. Forms work without client JavaScript. Refresh the page after the worker finishes an asynchronous operation. Main pages share tokens and layout in `static/app.css`.

Use **Findings queue** to filter the infrastructure backlog by status, priority or your assignments. A target page shows field-level diffs and its evidence timeline. See [the findings workflow](drift-operations.md).
