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


def test_terraform_budget_resource_defined_with_required_semantics():
    budget_file = REPO_ROOT / "infra" / "terraform" / "budget.tf"
    assert budget_file.exists()

    content = budget_file.read_text(encoding="utf-8")
    assert 'resource "aws_budgets_budget" "monthly_cost"' in content
    assert "var.enable_budget_alerts" in content
    assert 'budget_type  = "COST"' in content
    assert 'limit_unit   = "USD"' in content
    assert 'time_unit    = "MONTHLY"' in content
    assert "local.expected_aws_account_id" in content

    # Notification checks: 50% actual ($5), 80% actual ($8), 100% actual ($10), 100% forecasted ($10)
    assert 'threshold                  = 50' in content
    assert 'threshold                  = 80' in content
    assert 'threshold                  = 100' in content
    assert 'notification_type          = "ACTUAL"' in content
    assert 'notification_type          = "FORECASTED"' in content
    assert 'threshold_type             = "PERCENTAGE"' in content


def test_terraform_outputs_include_budget_and_console_link():
    outputs_file = REPO_ROOT / "infra" / "terraform" / "outputs.tf"
    assert outputs_file.exists()

    content = outputs_file.read_text(encoding="utf-8")
    assert 'output "budget_name"' in content
    assert 'output "budget_arn"' in content
    assert "budgets" in content


def test_work_history_entry_0011_registered():
    work_history_doc = REPO_ROOT / "docs" / "work-history" / "0011-aws-budget-alerts.md"
    readme_doc = REPO_ROOT / "docs" / "work-history" / "README.md"

    assert work_history_doc.exists()
    assert "0011" in readme_doc.read_text(encoding="utf-8")
