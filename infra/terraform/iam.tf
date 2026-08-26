data "aws_iam_policy_document" "ecs_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_task_execution" {
  name               = "${local.name}-ecs-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "ai_app_task" {
  name               = "${local.name}-ai-app-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

data "aws_iam_policy_document" "ai_app_task" {
  statement {
    sid    = "ApplicationState"
    effect = "Allow"
    actions = [
      "dynamodb:DeleteItem",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:UpdateItem",
    ]
    resources = [aws_dynamodb_table.application_state.arn]
  }

  statement {
    sid       = "ListArtifactBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.artifacts.arn]
  }

  statement {
    sid       = "ManageArtifacts"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.artifacts.arn}/*"]
  }

  dynamic "statement" {
    for_each = length(var.bedrock_model_arns) == 0 ? [] : [var.bedrock_model_arns]

    content {
      sid     = "InvokeConfiguredBedrockModels"
      effect  = "Allow"
      actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
      resources = statement.value
    }
  }
}

resource "aws_iam_role_policy" "ai_app_task" {
  name   = "${local.name}-ai-app-task"
  role   = aws_iam_role.ai_app_task.id
  policy = data.aws_iam_policy_document.ai_app_task.json
}

# The analytics MCP service deliberately has no AWS data permissions. Its
# bounded, read-only data access remains local DuckDB over public Parquet.
resource "aws_iam_role" "analytics_mcp_task" {
  name               = "${local.name}-analytics-mcp-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}
