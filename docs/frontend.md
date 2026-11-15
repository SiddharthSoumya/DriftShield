# Frontend design

The monitoring navigation groups **Repositories**, **Infrastructure** and **Findings**. Workspace-wide **Activity**, **Settings** and a setup guide have separate destinations. The EBS pilot uses **AWS EBS** as its entry point and links to the pilot guide. Workspace settings are visible to owners.

## Repositories

The repository overview shows configuration and scan status in separate columns. The detail page shows the last attempt, last successful observation, next scheduled attempt, and approved baseline. For first-time monitors, comparison becomes available after a successful scan. Errors preserve the last good observation, if one exists.

Scheduled scans require the background worker. The dashboard shows the last successful iteration of its GitHub stage and flags a missing or overdue check-in after five minutes. This timestamp describes the background loop. A successful observation has its own timestamp. Owners can open the JSON diagnostics. In local development, operators and owners can use **Scan now** for a selected monitor. Production requests queue for the worker. Both paths respect active leases and backoff.

## Infrastructure and findings

The target overview shows collector state, open findings and last successful observation. An empty workspace explains how to define a target, configure a collector and review the first observation. Follow [the collector guide](infrastructure.md) to run and schedule a collector.

Use **Findings** to filter the backlog by status, priority or assignment. Follow a finding to a field-level diff, review actions and evidence. A target links directly to coverage, findings, baseline history or approvals, evidence, and collector controls where available. The EBS pilot organizes enrollment and gap history under **Coverage**, two-person decisions under **Approvals**, and timeline or protected bundle access under **Evidence**. Server-side role, observation, review and activation gates govern available actions. Newly issued collector tokens appear once.

## Workspace

**Activity** is a timeline of operator actions. Expand event details to inspect the underlying audit data. **Settings** groups alerts, evidence retention, membership and delivery history. General deployments also show GitHub policies and App status. EBS pilot settings show the mailbox canary. Actual mailbox receipt and paging are verified by the customer monitor. Server CLI commands handle workspace provisioning and membership changes.

Server-rendered forms use Django permissions and CSRF protection. Navigation exposes the current section. Text accompanies status colors, keyboard focus is visible, and wide tables scroll within keyboard-focusable regions on narrow screens. Refresh to see updates after asynchronous scans or collector submissions. See [findings and remediation](drift-operations.md) and [local setup](../README.md#try-it-locally).
