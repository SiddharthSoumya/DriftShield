# Commercial deployment

## First offer

Offer a customer-hosted license with paid onboarding, verified upgrades, and support. The first buyer hypothesis is an engineering team or cloud consultancy operating AWS plus Terraform or OpenTofu that wants one place to review out-of-band configuration changes. Use EBS storage increases, instance resizing, or new NAT gateways as test cases. The customer keeps platform identities and plan files in its infrastructure, which reduces the trust and procurement burden for a pilot.

The current repository is MIT licensed. Existing MIT copies remain available under those terms. Charge for supported releases, installation, and support. Productize billing or add a separately licensed premium component only after establishing a buyer and obtaining appropriate licensing advice.

## Hosted option

After recurring use, offer a managed control plane with the same outbound collectors. Hosting can simplify upgrades and onboarding. It also makes the operator responsible for tenant isolation, incident response, data retention, recovery, availability and customer procurement. Start with dedicated deployments or a limited hosted pilot. Test the tenant boundary independently before shared hosting at scale.

GitHub already provides organization-level ruleset insights. Safe Settings can preview and apply GitHub settings. Spacelift and Firefly cover IaC drift and remediation, and AWS Config offers native rules and remediation. DriftShield's proposed position is one review and verification workflow across selected GitHub, IaC, cloud and runtime observations. Paid customer demand for that combination is still unverified. [GitHub rule insights](https://github.blog/changelog/2026-08-25-rule-insights-dashboard-generally-available/) · [Safe Settings](https://github.com/github-community-projects/safe-settings) · [Spacelift](https://docs.spacelift.io/concepts/stack/drift-detection) · [AWS Config](https://docs.aws.amazon.com/config/latest/developerguide/remediation.html)

## Acceptance criteria for a paid pilot

Have two buyers connect real staging environments, review and resolve a supported drift event, and agree to pay for the workflow. Track time to first actionable finding, false positives caused by expected scaling, time from detection to verified resolution, and which exported evidence they actually use. Price by monitored scopes or environments initially. More providers and direct remediation should follow paid demand and provider-specific safety testing.
