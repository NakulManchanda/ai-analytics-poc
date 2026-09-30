# Architectural decision records

This directory holds durable architectural decisions that outlive individual pull requests.
Use short, numbered Markdown records such as `0001-app-owns-llm-loop.md` with context, decision,
alternatives, and consequences. Do not create ADRs for routine implementation details.

Pull-request chronology belongs in `docs/work-history/`; the active session handoff belongs in
`docs/progress.md`.

## Index

- [ADR 0006 — Allow the existing local AWS profile for Terraform operations](0006-local-terraform-operator-profile.md)
- [ADR 0007 — Telemetry, CloudWatch Metrics, and Local Comparison Architecture](0007-telemetry-metrics-comparison-architecture.md)
- [ADR 0008 — V4 Voice Input: WebSocket + Continuous Chunking Architecture](0008-v4-voice-input-websocket-architecture.md)
- [ADR 0009 — V5 Voice Output: AWS Polly TTS with Full-Answer Synthesis](0009-v5-voice-output-polly-tts.md)
- [ADR 0010 — Package the Lambda inference lab as one remote bundle](0010-transferable-lambda-inference-lab.md)
- [ADR 0011 — Compare the custom router against Dynamo's KV-aware router (Proposed)](0011-dynamo-kv-router-comparison.md)
