# Work 0005 — Applied AWS console links

## Goal

Replace the post-apply placeholder in `docs/aws-console-links.md` with the
resolved AWS Console links from the applied Terraform state.

## Starting state

`origin/main` at `1a2986e` contained only instructions for obtaining links
after apply. The external Terraform state recorded an applied foundation in
account `107207236011`, Region `us-east-1`.

## Decisions

- Keep the change documentation-only and include every `console_links` output.
- Record the applied date and account/Region context without exposing state
  contents, credentials, or identifiers other than the public AWS resource IDs
  embedded in the Console URLs.
- Retain an exact, read-only refresh command with the explicit external state
  path.

## Verification

- Initialized Terraform with the backend disabled in a temporary data directory.
- Ran `terraform output -state="${HOME}/.local/state/ai-analytics-poc/terraform.tfstate" -json console_links` using `AWS_PROFILE=default`.
- Checked all 17 documented Markdown destinations use `https` AWS Console URLs.

## Pull request and merge

Merged PR [#11](https://github.com/NakulManchanda/ai-analytics-poc/pull/11) into `main` at
merge commit `19c57c8`.

## Lessons

Terraform outputs are the authoritative source for applied resource Console
URLs; documentation should be refreshed from that output rather than guessed.
