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

output "console_links" {
  description = "Resolved AWS Console URLs for the applied foundation in the verified account and configured Region."
  value = {
    vpc                     = "https://${var.aws_region}.console.aws.amazon.com/vpc/home?region=${var.aws_region}#vpcs:VpcId=${aws_vpc.main.id}"
    public_subnet_1         = "https://${var.aws_region}.console.aws.amazon.com/vpc/home?region=${var.aws_region}#subnets:subnetId=${aws_subnet.public[0].id}"
    public_subnet_2         = "https://${var.aws_region}.console.aws.amazon.com/vpc/home?region=${var.aws_region}#subnets:subnetId=${aws_subnet.public[1].id}"
    private_subnet_1        = "https://${var.aws_region}.console.aws.amazon.com/vpc/home?region=${var.aws_region}#subnets:subnetId=${aws_subnet.private[0].id}"
    private_subnet_2        = "https://${var.aws_region}.console.aws.amazon.com/vpc/home?region=${var.aws_region}#subnets:subnetId=${aws_subnet.private[1].id}"
    ecs_task_security_group = "https://${var.aws_region}.console.aws.amazon.com/ec2/home?region=${var.aws_region}#SecurityGroup:groupId=${aws_security_group.ecs_tasks.id}"

    ai_app_ecr        = "https://${var.aws_region}.console.aws.amazon.com/ecr/repositories/private/${urlencode(aws_ecr_repository.ai_app.name)}?region=${var.aws_region}"
    analytics_mcp_ecr = "https://${var.aws_region}.console.aws.amazon.com/ecr/repositories/private/${urlencode(aws_ecr_repository.analytics_mcp.name)}?region=${var.aws_region}"
    frontend_bucket   = "https://s3.console.aws.amazon.com/s3/buckets/${urlencode(aws_s3_bucket.frontend.bucket)}?region=${var.aws_region}&tab=objects"
    artifact_bucket   = "https://s3.console.aws.amazon.com/s3/buckets/${urlencode(aws_s3_bucket.artifacts.bucket)}?region=${var.aws_region}&tab=objects"

    application_state_table = "https://${var.aws_region}.console.aws.amazon.com/dynamodbv2/home?region=${var.aws_region}#table?name=${urlencode(aws_dynamodb_table.application_state.name)}"
    ecs_cluster             = "https://${var.aws_region}.console.aws.amazon.com/ecs/v2/clusters/${urlencode(aws_ecs_cluster.main.name)}/services?region=${var.aws_region}"

    ecs_task_execution_role = "https://console.aws.amazon.com/iam/home#/roles/details/${urlencode(aws_iam_role.ecs_task_execution.name)}"
    ai_app_task_role        = "https://console.aws.amazon.com/iam/home#/roles/details/${urlencode(aws_iam_role.ai_app_task.name)}"
    analytics_mcp_task_role = "https://console.aws.amazon.com/iam/home#/roles/details/${urlencode(aws_iam_role.analytics_mcp_task.name)}"

    ai_app_log_group        = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#logsV2:log-groups/log-group/${urlencode(aws_cloudwatch_log_group.ai_app.name)}"
    analytics_mcp_log_group = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#logsV2:log-groups/log-group/${urlencode(aws_cloudwatch_log_group.analytics_mcp.name)}"
    budgets                 = "https://console.aws.amazon.com/costmanagement/home#/budgets"
    alb                     = "https://${var.aws_region}.console.aws.amazon.com/ec2/home?region=${var.aws_region}#LoadBalancers:loadBalancerArn=${aws_lb.main.arn}"
    cloudfront_distribution = "https://console.aws.amazon.com/cloudfront/v4/home#/distributions/${aws_cloudfront_distribution.main.id}"
  }
}

output "alb_dns_name" {
  description = "Public DNS name of the Application Load Balancer."
  value       = aws_lb.main.dns_name
}

output "alb_arn" {
  description = "ARN of the Application Load Balancer."
  value       = aws_lb.main.arn
}

output "ai_app_service_name" {
  description = "Name of the ai-app ECS service."
  value       = aws_ecs_service.ai_app.name
}

output "analytics_mcp_service_name" {
  description = "Name of the analytics-mcp ECS service."
  value       = aws_ecs_service.analytics_mcp.name
}

output "service_connect_namespace" {
  description = "Private Service Connect namespace."
  value       = aws_service_discovery_http_namespace.main.name
}

output "cloudfront_domain_name" {
  description = "Public domain name of the CloudFront distribution."
  value       = aws_cloudfront_distribution.main.domain_name
}

output "cloudfront_distribution_id" {
  description = "ID of the CloudFront distribution."
  value       = aws_cloudfront_distribution.main.id
}

output "cloudfront_url" {
  description = "Full HTTPS URL for the public CloudFront frontend entry point."
  value       = "https://${aws_cloudfront_distribution.main.domain_name}"
}

output "custom_domain_urls" {
  description = "Custom domain HTTPS URLs mapped via Route 53 and ACM."
  value = var.enable_custom_domain ? [
    "https://${var.custom_subdomain_name}"
  ] : []
}

output "budget_name" {
  description = "Name of the monthly AWS cost budget, if enabled."
  value       = try(aws_budgets_budget.monthly_cost[0].name, null)
}

output "budget_arn" {
  description = "ARN of the monthly AWS cost budget, if enabled."
  value       = try(aws_budgets_budget.monthly_cost[0].arn, null)
}

output "terraform_operator_account_id" {
  description = "AWS account enforced by this single-user POC Terraform foundation."
  value       = local.expected_aws_account_id
}
