import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / ".github/workflows/bedrock-eval-smoke.yml"
PINNED_SHA = re.compile(r"^[^@]+@[0-9a-f]{40}(\s+#\s+v\d+)?$")


def test_bedrock_eval_workflow_has_manual_dispatch_and_protected_env() -> None:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))

    assert triggers == {"workflow_dispatch": triggers["workflow_dispatch"]}
    assert "pull_request" not in triggers
    assert "pull_request_target" not in triggers

    assert workflow["permissions"] == {"contents": "read", "id-token": "write"}

    job = workflow["jobs"]["bedrock-smoke"]
    assert job["environment"] == "bedrock-eval-protected"
    assert job["timeout-minutes"] == 45


def test_bedrock_eval_workflow_pins_third_party_actions_and_uses_oidc() -> None:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["bedrock-smoke"]["steps"]

    uses_values = [step["uses"] for step in steps if "uses" in step]
    assert uses_values, "expected pinned actions in workflow"
    for uses in uses_values:
        assert PINNED_SHA.match(uses), f"action must be SHA pinned: {uses}"

    aws_step = next(
        step for step in steps if step.get("name") == "Configure AWS credentials (OIDC, short-lived)"
    )
    with_fields = aws_step["with"]
    assert with_fields["role-duration-seconds"] == "${{ env.AWS_ROLE_DURATION_SECONDS }}"
    assert with_fields["role-to-assume"] == "${{ vars.BEDROCK_EVAL_ROLE_ARN }}"


def test_bedrock_eval_workflow_guards_trusted_refs() -> None:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["bedrock-smoke"]["steps"]

    guard_step = next(
        step for step in steps if step.get("name") == "Validate trusted dispatch inputs and repository"
    )
    guard_script = guard_step["run"]
    assert 'test "${GITHUB_REPOSITORY}" = "NakulManchanda/ai-analytics-poc"' in guard_script
    assert 'release_ref must be main or a v* tag' in guard_script
