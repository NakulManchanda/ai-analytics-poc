output "ecs_cluster_name" {
  description = "Name of the ECS cluster for later service deployment."
  value       = aws_ecs_cluster.main.name
}

output "ecr_repository_urls" {
  description = "Repository URLs for the ai-app and analytics-mcp images."
  value = {
    ai_app        = aws_ecr_repository.ai_app.repository_url
    analytics_mcp = aws_ecr_repository.analytics_mcp.repository_url
  }
}

output "frontend_bucket_name" {
  description = "Private S3 bucket reserved for the React build."
  value       = aws_s3_bucket.frontend.bucket
}

output "artifact_bucket_name" {
  description = "Private S3 bucket for generated application artifacts."
  value       = aws_s3_bucket.artifacts.bucket
}

output "application_state_table_name" {
  description = "DynamoDB table reserved for durable application state."
  value       = aws_dynamodb_table.application_state.name
}

output "private_subnet_ids" {
  description = "Private subnet IDs reserved for later ECS tasks."
  value       = aws_subnet.private[*].id
}

output "ecs_task_security_group_id" {
  description = "Ingress-free security group reserved for later ECS tasks."
  value       = aws_security_group.ecs_tasks.id
}

output "iam_role_arns" {
  description = "Separate execution and task role ARNs."
  value = {
    ecs_task_execution = aws_iam_role.ecs_task_execution.arn
    ai_app_task        = aws_iam_role.ai_app_task.arn
    analytics_mcp_task = aws_iam_role.analytics_mcp_task.arn
  }
}
