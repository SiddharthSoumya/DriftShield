# Product, market and release scope

## Buyer and workflow

The initial buyer is a platform lead, security-minded engineering manager, or development agency managing 10–100 GitHub repositories. They need to detect changes to approved settings and active rules, route the findings to a team, and produce evidence for periodic reviews. The onboarding path is connect an installation, import selected repositories, approve the first observation, and receive a real alert after a sandbox change.

## Market research

| Alternative | Its core job | Where DriftShield fits |
| --- | --- | --- |
| [GitHub rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets) | Enforce branch and tag policy in GitHub | Track observed changes to active default-branch rules and record their resolution |
| [Legitify](https://www.legitsecurity.com/legitify) | Scan GitHub and GitLab for a wide range of misconfigurations | Maintain an approved repository baseline, acknowledge findings, and collect historical evidence |
| [StepSecurity](https://github.com/apps/stepsecurity-app) | Secure CI/CD workflows and evaluate security posture | Focus on repository settings and rules drift in a compact self-hosted product |
| [Rewind](https://rewind.com/products/backups/github/) | Back up and restore GitHub data and metadata | Surface setting changes and provide an approval and acknowledgement record |

This is desk research. It establishes existing alternatives and informs the feature choice. Pricing, adoption and differentiation still need customer evidence.

## What ships

GitHub.com public and installation-scoped private repository collection, settings/ruleset diffs, three optional workspace policies, selective approval and acknowledgements, audit history, CSV evidence, health and freshness, email and weekly digests, workspace roles and server-managed provisioning, PostgreSQL and Linux systemd deployment.

## Commercial pilot

Recruit five teams that own at least ten repositories and already review repository policy. Track median time from account setup to first approved baseline, percentage of monitors with a fresh scan, percentage of findings that required action, and weekly active workspaces after one month. Ask each team to export evidence and describe which existing workflow it would replace. Test a manual invoice offer at $29 per workspace per month for up to 25 repositories. This is a pricing hypothesis. Use the server-side repository allowance as the pilot entitlement.

## Future scope

Prioritize customer-driven coverage for classic branch protections and ruleset bypass, GitHub App webhooks with reconciliation, Slack delivery, per-repository policy exceptions, and owner routing. Add MFA/SSO, identity-based App onboarding, database-level tenant isolation, self-service billing, and independently tested disaster recovery before operating an open hosted SaaS. Add more providers only after teams confirm the same approval and evidence workflow is valuable on those systems.
