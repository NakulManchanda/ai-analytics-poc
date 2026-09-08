# Architectural decision records

This directory holds durable architectural decisions that outlive individual pull requests.
Use short, numbered Markdown records such as `0001-app-owns-llm-loop.md` with context, decision,
alternatives, and consequences. Do not create ADRs for routine implementation details.

Pull-request chronology belongs in `docs/work-history/`; the active session handoff belongs in
`docs/progress.md`.

## Index

- [ADR 0006 — Allow the existing local AWS profile for Terraform operations](0006-local-terraform-operator-profile.md)
- [ADR 0007 — Telemetry, CloudWatch Metrics, and Local Comparison Architecture](0007-telemetry-metrics-comparison-architecture.md)
