import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / ".github/workflows/bedrock-eval-smoke.yml"
PINNED_SHA = re.compile(r"^[^@]+@[0-9a-f]{40}(\s+#\s+v\d+)?$")


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _load_workflow()["jobs"]["bedrock-smoke"]["steps"]


def _step(name: str) -> dict:
    return next(step for step in _steps() if step.get("name") == name)


def _run_guard(script: str, *, split: str, ref: str) -> subprocess.CompletedProcess:
    env = {
        "GITHUB_REPOSITORY": "NakulManchanda/ai-analytics-poc",
        "GITHUB_REF": ref,
        "MAIN_REF": "refs/heads/main",
        "DISPATCH_EVAL_SPLIT": split,
    }
    return subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
    )


def test_bedrock_eval_workflow_has_manual_dispatch_and_protected_env() -> None:
    workflow = _load_workflow()
    triggers = workflow.get("on", workflow.get(True))

    assert triggers == {"workflow_dispatch": triggers["workflow_dispatch"]}
    assert workflow["permissions"] == {"contents": "read", "id-token": "write"}

    dispatch_inputs = triggers["workflow_dispatch"]["inputs"]
    assert sorted(dispatch_inputs) == ["eval_split"]

    job = workflow["jobs"]["bedrock-smoke"]
    assert job["environment"] == "bedrock-eval-protected"
    assert job["timeout-minutes"] == 45


def test_guard_script_enforces_main_ref_and_allowed_eval_split() -> None:
    guard_script = _step("Validate trusted dispatch context")["run"]

    ok = _run_guard(guard_script, split="dev", ref="refs/heads/main")
    assert ok.returncode == 0

    bad_ref = _run_guard(guard_script, split="dev", ref="refs/heads/feature/unsafe")
    assert bad_ref.returncode != 0

    bad_split = _run_guard(guard_script, split="bad-split", ref="refs/heads/main")
    assert bad_split.returncode != 0


def test_bedrock_eval_workflow_checks_out_main_and_verifies_provenance() -> None:
    checkout = _step("Check out protected main")
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["ref"] == "refs/heads/main"

    provenance = _step("Verify checked-out main provenance")["run"]
    assert 'test "${GITHUB_REF}" = "${MAIN_REF}"' in provenance
    assert 'test "$(git rev-parse origin/main)" = "$(git rev-parse HEAD)"' in provenance


def test_bedrock_eval_workflow_pins_third_party_actions_and_uses_oidc() -> None:
    steps = _steps()

    uses_values = [step["uses"] for step in steps if "uses" in step]
    assert uses_values, "expected pinned actions in workflow"
    for uses in uses_values:
        assert PINNED_SHA.match(uses), f"action must be SHA pinned: {uses}"

    aws_step = _step("Configure AWS credentials (OIDC, short-lived)")
    with_fields = aws_step["with"]
    assert with_fields["role-duration-seconds"] == "${{ env.AWS_ROLE_DURATION_SECONDS }}"
    assert with_fields["role-to-assume"] == "${{ vars.BEDROCK_EVAL_ROLE_ARN }}"


def test_bedrock_eval_workflow_uploads_only_sanitized_artifacts() -> None:
    upload = _step("Upload sanitized eval summary")["with"]
    assert "artifacts/bedrock-eval-smoke/summary.json" in upload["path"]
    assert upload["if-no-files-found"] == "error"
