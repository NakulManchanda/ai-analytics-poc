from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_iam_policy_includes_bedrock_streaming_action():
    iam_tf = REPO_ROOT / "infra" / "terraform" / "iam.tf"
    assert iam_tf.exists(), f"Missing {iam_tf}"
    content = iam_tf.read_text(encoding="utf-8")

    assert "bedrock:InvokeModel" in content
    assert "bedrock:InvokeModelWithResponseStream" in content
