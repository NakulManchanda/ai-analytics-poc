# Current milestone

Repository bootstrap — before Milestone 0

## Status

IN PROGRESS

## Acceptance criteria

- [x] Governance and skeleton files created
- [x] Requirements document preserved locally and excluded from the bootstrap commit
- [x] No application functionality or `/health` endpoint added
- [ ] Local Git repository initialized on `main`
- [ ] GitHub repository created with user-selected name and visibility
- [ ] Bootstrap commit pushed to `main`
- [ ] `main` branch protection configured

## Decisions

- Bootstrap contains only the 11 allowlisted files from the requirements; the requirements
  source itself remains an explicitly ignored local execution input.
- The root `pyproject.toml` defines shared Python/tooling policy; service dependencies begin in
  the milestone that introduces each service.
- Terraform example values are non-secret placeholders; actual Terraform variables arrive in
  Milestone 13.

## Known limitations

- No application is runnable yet by design.
- Git and GitHub setup require the repository name and visibility decision.
- AWS, GitHub, and Bedrock access checks are coordinator-owned and remain pending.

## Next milestone

Milestone 0 — FastAPI health endpoint. Do not start until requested and the repository
bootstrap is committed, pushed, and protected.
