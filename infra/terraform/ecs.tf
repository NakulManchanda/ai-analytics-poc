resource "aws_ecs_cluster" "main" {
  name = "${local.name}-cluster"

  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_cloudwatch_log_group" "ai_app" {
  name              = "/ecs/${local.name}-ai-app"
  retention_in_days = var.log_retention_in_days
}

resource "aws_cloudwatch_log_group" "analytics_mcp" {
  name              = "/ecs/${local.name}-analytics-mcp"
  retention_in_days = var.log_retention_in_days
}
