import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def extract_notification_blocks(hcl_text: str) -> list[dict[str, str]]:
    """Extract static `notification { ... }` block attributes from HCL text.

    Returns a list of dictionaries with normalized attribute keys and values.
    """
    pattern = re.compile(r"notification\s*\{([^}]+)\}", re.MULTILINE)
    blocks: list[dict[str, str]] = []
    for match in pattern.finditer(hcl_text):
        block_body = match.group(1)
        attrs: dict[str, str] = {}
        for line in block_body.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                key = k.strip()
                val = v.strip().strip('"').strip("'")
                attrs[key] = val
        if attrs:
            blocks.append(attrs)
    return blocks


def test_terraform_budget_notifications_exact_pairings():
    budget_file = REPO_ROOT / "infra" / "terraform" / "budget.tf"
    assert budget_file.exists(), f"Missing budget file at {budget_file}"

    content = budget_file.read_text(encoding="utf-8")
    notifications = extract_notification_blocks(content)

    # Must contain exactly 4 notification blocks
    assert (
        len(notifications) == 4
    ), f"Expected exactly 4 notification blocks, found {len(notifications)}"

    expected_notifications = [
        {
            "comparison_operator": "GREATER_THAN",
            "threshold": "5",
            "threshold_type": "ABSOLUTE_VALUE",
            "notification_type": "ACTUAL",
            "subscriber_email_addresses": "[var.budget_alert_email]",
        },
        {
            "comparison_operator": "GREATER_THAN",
            "threshold": "8",
            "threshold_type": "ABSOLUTE_VALUE",
            "notification_type": "ACTUAL",
            "subscriber_email_addresses": "[var.budget_alert_email]",
        },
        {
            "comparison_operator": "GREATER_THAN",
            "threshold": "10",
            "threshold_type": "ABSOLUTE_VALUE",
            "notification_type": "ACTUAL",
            "subscriber_email_addresses": "[var.budget_alert_email]",
        },
        {
            "comparison_operator": "GREATER_THAN",
            "threshold": "10",
            "threshold_type": "ABSOLUTE_VALUE",
            "notification_type": "FORECASTED",
            "subscriber_email_addresses": "[var.budget_alert_email]",
        },
    ]

    for idx, (actual, expected) in enumerate(
        zip(notifications, expected_notifications, strict=True)
    ):
        assert (
            actual.get("comparison_operator") == expected["comparison_operator"]
        ), f"Notification block {idx} comparison_operator mismatch: {actual}"
        assert float(actual.get("threshold", "0")) == float(
            expected["threshold"]
        ), f"Notification block {idx} threshold mismatch: {actual}"
        assert (
            actual.get("threshold_type") == expected["threshold_type"]
        ), f"Notification block {idx} threshold_type mismatch: {actual}"
        assert (
            actual.get("notification_type") == expected["notification_type"]
        ), f"Notification block {idx} notification_type mismatch: {actual}"
        assert (
            actual.get("subscriber_email_addresses")
            == expected["subscriber_email_addresses"]
        ), f"Notification block {idx} subscriber_email_addresses mismatch: {actual}"


def test_terraform_budget_resource_configuration():
    budget_file = REPO_ROOT / "infra" / "terraform" / "budget.tf"
    assert budget_file.exists()

    content = budget_file.read_text(encoding="utf-8")
    assert 'resource "aws_budgets_budget" "monthly_cost"' in content
    assert "count = var.enable_budget_alerts ? 1 : 0" in content
    assert 'budget_type  = "COST"' in content
    assert "limit_amount = var.monthly_budget_limit_usd" in content
    assert 'limit_unit   = "USD"' in content
    assert 'time_unit    = "MONTHLY"' in content
    assert "account_id = local.expected_aws_account_id" in content

    # Enforce account and email preconditions
    assert (
        "data.aws_caller_identity.current.account_id == local.expected_aws_account_id"
        in content
    )
    assert 'var.budget_alert_email != null && var.budget_alert_email != ""' in content


def test_terraform_budget_variables_defined():
    var_file = REPO_ROOT / "infra" / "terraform" / "variables.tf"
    assert var_file.exists()

    content = var_file.read_text(encoding="utf-8")
    assert 'variable "enable_budget_alerts"' in content
    assert 'variable "budget_alert_email"' in content
    assert 'variable "monthly_budget_limit_usd"' in content

    # Check budget_alert_email has default null and validation regex
    email_block_match = re.search(
        r'variable\s+"budget_alert_email"\s*\{([^}]+validation\s*\{[^}]+\}[^}]*)\}',
        content,
    )
    assert (
        email_block_match is not None
    ), "Could not extract variable budget_alert_email block"
    email_block = email_block_match.group(1)
    assert "default     = null" in email_block or "default = null" in email_block
    assert "regex(" in email_block

    # Check monthly_budget_limit_usd has default "10.0" and validation condition
    # requiring a positive numeric amount
    limit_block_match = re.search(
        r'variable\s+"monthly_budget_limit_usd"\s*\{([^}]+validation\s*\{[^}]+\}[^}]*)\}',
        content,
    )
    assert (
        limit_block_match is not None
    ), "Could not extract variable monthly_budget_limit_usd block"
    limit_block = limit_block_match.group(1)
    assert 'default     = "10.0"' in limit_block or 'default = "10.0"' in limit_block
    assert "tonumber(" in limit_block
    assert "> 0" in limit_block


def test_terraform_monthly_budget_limit_validation():
    var_file = REPO_ROOT / "infra" / "terraform" / "variables.tf"
    assert var_file.exists()

    content = var_file.read_text(encoding="utf-8")
    limit_block_match = re.search(
        r'variable\s+"monthly_budget_limit_usd"\s*\{([^}]+validation\s*\{[^}]+\}[^}]*)\}',
        content,
    )
    assert limit_block_match is not None
    limit_block = limit_block_match.group(1)

    # Condition must safely parse number and require strictly positive value
    assert "can(tonumber(var.monthly_budget_limit_usd))" in limit_block
    assert "tonumber(var.monthly_budget_limit_usd) > 0" in limit_block

    def evaluate_static_limit_condition(val: str) -> bool:
        # Static simulation of the Terraform validation expression:
        # can(regex("^[0-9]+(\\.[0-9]{1,2})?$", val)) && can(tonumber(val)) && tonumber(val) > 0
        regex_match = bool(re.match(r"^[0-9]+(\.[0-9]{1,2})?$", val))
        if not regex_match:
            return False
        try:
            num = float(val)
            return num > 0
        except (ValueError, TypeError):
            return False

    # Valid positive dollar inputs
    valid_inputs = ["10.0", "10", "5.00", "0.01", "100.5", "1234.56"]
    for val in valid_inputs:
        assert (
            evaluate_static_limit_condition(val) is True
        ), f"Expected {val} to be valid, but was rejected"

    # Zero-valued inputs flagged by Copilot must be rejected
    zero_inputs = ["0", "00", "0.0", "0.00", "00.00"]
    for val in zero_inputs:
        assert (
            evaluate_static_limit_condition(val) is False
        ), f"Expected zero value {val} to be rejected, but was accepted"

    # Negative and malformed inputs must be rejected
    invalid_inputs = ["-1", "-0.01", "-10.0", "", "abc", "10.001", " 10.0", "$10.00"]
    for val in invalid_inputs:
        assert (
            evaluate_static_limit_condition(val) is False
        ), f"Expected invalid value {val} to be rejected, but was accepted"


def test_no_personal_email_committed():
    files_to_check = [
        REPO_ROOT / "infra" / "terraform" / "variables.tf",
        REPO_ROOT / "infra" / "terraform" / "terraform.tfvars.example",
        REPO_ROOT / "infra" / "terraform" / "budget.tf",
        REPO_ROOT / "terraform.tfvars.example",
    ]
    for file_path in files_to_check:
        assert file_path.exists(), f"Expected file {file_path} to exist"
        content = file_path.read_text(encoding="utf-8")
        assert (
            "nakul1986@gmail.com" not in content
        ), f"Personal email found committed in {file_path.relative_to(REPO_ROOT)}"


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


def test_bedrock_iam_allowlist_is_pinned_to_ai_app_only():
    variables_file = REPO_ROOT / "infra" / "terraform" / "variables.tf"
    iam_file = REPO_ROOT / "infra" / "terraform" / "iam.tf"

    variables = variables_file.read_text(encoding="utf-8")
    iam = iam_file.read_text(encoding="utf-8")
    allowed_model_arn = (
        "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-micro-v1:0"
    )

    assert 'variable "bedrock_model_arns"' in variables
    assert variables.count(allowed_model_arn) == 2
    assert 'data "aws_iam_policy_document" "ai_app_task"' in iam
    assert 'actions   = ["bedrock:InvokeModel"]' in iam
    assert "resources = statement.value" in iam

    mcp_role_start = iam.index('resource "aws_iam_role" "analytics_mcp_task"')
    assert "bedrock:InvokeModel" not in iam[mcp_role_start:]
