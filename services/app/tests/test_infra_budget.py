from pathlib import Path
import re

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_root_terraform_tfvars_example_points_to_infra():
    root_tfvars = REPO_ROOT / "terraform.tfvars.example"
    infra_tfvars = REPO_ROOT / "infra" / "terraform" / "terraform.tfvars.example"

    assert root_tfvars.exists()
    assert infra_tfvars.exists()

    content = root_tfvars.read_text(encoding="utf-8")
    assert "infra/terraform/terraform.tfvars.example" in content
    # Ensure root tfvars does not define divergent variable values
    assert "app_image_tag" not in content
    assert "demo_global_llm_budget_usd" not in content


def test_terraform_budget_variables_defined():
    var_file = REPO_ROOT / "infra" / "terraform" / "variables.tf"
    assert var_file.exists()

    content = var_file.read_text(encoding="utf-8")
    assert 'variable "enable_budget_alerts"' in content
    assert 'variable "budget_alert_email"' in content
    assert 'variable "monthly_budget_limit_usd"' in content


def test_work_history_entry_0011_registered():
    work_history_doc = REPO_ROOT / "docs" / "work-history" / "0011-aws-budget-alerts.md"
    readme_doc = REPO_ROOT / "docs" / "work-history" / "README.md"

    assert work_history_doc.exists()
    assert "0011" in readme_doc.read_text(encoding="utf-8")
