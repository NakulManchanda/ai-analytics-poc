# 0048 — Next-session roadmap refresh

## Goal

Refresh the operational handoff after the AWS cost-control and shared Bedrock
allowance work, and make issue #89 the next explicit task.

## Starting point

PRs #87 and #90 were merged. AWS was verified parked, while the handoff still
described the older always-on v3 deployment and sent the next agent directly to
the voice milestone. Issue #89 had interrupted, uncommitted work in its assigned
worktree.

## Decisions

- Lead with actual parked state and link to the recorded verification evidence.
- Distinguish merged code, deployed code, local fake mode, and opt-in real
  Bedrock mode.
- Preserve the interrupted issue #89 worktree and make remote state plus secure
  release-driven deployment the next task.
- Defer voice/multimodal work until that infrastructure-control task completes.
- Record the public-repository confidentiality boundary for Terraform state,
  plans, variables, identifiers, and workflow output.

## Verification and status

- Documentation paths and referenced issue/PR states were checked against the
  repository and GitHub.
- `git diff --check` passed.
- Documentation-only PR pending; no infrastructure or application change.

## Lesson

A durable handoff must identify operational reality separately from historical
release evidence, especially when merged code is intentionally not deployed.
