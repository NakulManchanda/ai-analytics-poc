resource "aws_budgets_budget" "monthly_cost" {
  count = var.enable_budget_alerts ? 1 : 0

  name         = "${local.name}-monthly-cost"
  budget_type  = "COST"
  limit_amount = var.monthly_budget_limit_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  account_id = local.expected_aws_account_id

  # $5 actual spend alert
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 5
    threshold_type             = "ABSOLUTE_VALUE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  # $8 actual spend alert
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 8
    threshold_type             = "ABSOLUTE_VALUE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  # $10 actual spend alert
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 10
    threshold_type             = "ABSOLUTE_VALUE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  # $10 forecasted spend alert (note: forecast alerts require sufficient historical usage data)
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 10
    threshold_type             = "ABSOLUTE_VALUE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == local.expected_aws_account_id
      error_message = "Refusing to create the POC budget outside AWS account ${local.expected_aws_account_id}."
    }
    precondition {
      condition     = var.budget_alert_email != null && var.budget_alert_email != ""
      error_message = "budget_alert_email must be configured when enable_budget_alerts is true."
    }
  }
}
