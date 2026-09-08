from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_main_terraform_uses_native_s3_locking_on_a_supported_version() -> None:
    versions = (REPO_ROOT / "infra/terraform/versions.tf").read_text()
    assert 'required_version = ">= 1.16.1, < 2.0.0"' in versions
    assert 'backend "s3"' in versions
    assert "use_lockfile = true" in versions


def test_bootstrap_state_bucket_is_private_versioned_encrypted_and_tls_only() -> None:
    bootstrap = (REPO_ROOT / "infra/terraform/bootstrap/state.tf").read_text()
    assert 'resource "aws_s3_bucket" "terraform_state"' in bootstrap
    assert 'resource "aws_s3_bucket_versioning" "terraform_state"' in bootstrap
    assert (
        'resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state"'
        in bootstrap
    )
    assert 'resource "aws_s3_bucket_public_access_block" "terraform_state"' in bootstrap
    assert "block_public_acls       = true" in bootstrap
    assert 'sid    = "DenyInsecureTransport"' in bootstrap
    assert 'variable = "aws:SecureTransport"' in bootstrap


def test_release_apply_is_protected_and_never_publishes_sensitive_terraform_data() -> (
    None
):
    workflow = (REPO_ROOT / ".github/workflows/terraform-release.yml").read_text()
    assert "workflow_dispatch:" in workflow
    assert "environment: terraform-production" in workflow
    assert "id-token: write" in workflow
    assert "aws-actions/configure-aws-credentials" in workflow
    assert "terraform apply -input=false terraform-release.tfplan" in workflow
    assert "terraform show -json" in workflow
    assert "actions/upload-artifact" not in workflow
    assert "terraform show terraform-release.tfplan" not in workflow
    assert "set +x" in workflow


def test_public_terraform_validation_has_no_cloud_credentials_or_remote_state() -> None:
    workflow = (REPO_ROOT / ".github/workflows/terraform.yml").read_text()
    assert "init-backendless" in workflow
    assert "configure-aws-credentials" not in workflow
    assert "id-token: write" not in workflow


def test_dynamodb_table_defines_gsi_for_metrics_and_runs() -> None:
    dynamodb_tf = (REPO_ROOT / "infra/terraform/dynamodb.tf").read_text()
    assert 'name            = "entity_type-started_at-index"' in dynamodb_tf
    assert 'hash_key        = "entity_type"' in dynamodb_tf
    assert 'range_key       = "started_at"' in dynamodb_tf
    assert 'projection_type = "ALL"' in dynamodb_tf
