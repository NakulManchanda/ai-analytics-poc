resource "aws_ecs_cluster" "main" {
  name = "${local.name}-cluster"

  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_service_discovery_http_namespace" "main" {
  name        = "${local.name}.local"
  description = "Private Service Connect namespace for ${local.name}"

  tags = {
    Name = "${local.name}-service-connect"
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

resource "aws_ecs_task_definition" "analytics_mcp" {
  family                   = "${local.name}-analytics-mcp"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.analytics_mcp_cpu)
  memory                   = tostring(var.analytics_mcp_memory)
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.analytics_mcp_task.arn

  container_definitions = jsonencode([
    {
      name      = "analytics-mcp"
      image     = "${aws_ecr_repository.analytics_mcp.repository_url}:${var.analytics_mcp_image_tag}"
      essential = true
      portMappings = [
        {
          name          = "analytics-mcp"
          containerPort = 8001
          hostPort      = 8001
          protocol      = "tcp"
          appProtocol   = "http"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.analytics_mcp.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])
}

resource "aws_ecs_task_definition" "ai_app" {
  family                   = "${local.name}-ai-app"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.ai_app_cpu)
  memory                   = tostring(var.ai_app_memory)
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ai_app_task.arn

  container_definitions = jsonencode([
    {
      name      = "ai-app"
      image     = "${aws_ecr_repository.ai_app.repository_url}:${var.ai_app_image_tag}"
      essential = true
      portMappings = [
        {
          name          = "ai-app"
          containerPort = 8080
          hostPort      = 8080
          protocol      = "tcp"
          appProtocol   = "http"
        }
      ]
      environment = [
        {
          name  = "DYNAMODB_TABLE_NAME"
          value = aws_dynamodb_table.application_state.name
        },
        {
          name  = "ARTIFACTS_BUCKET_NAME"
          value = aws_s3_bucket.artifacts.id
        },
        {
          name  = "MCP_SERVER_URL"
          value = "http://analytics-mcp:8001/mcp"
        },
        {
          name  = "MCP_URL"
          value = "http://analytics-mcp:8001/mcp"
        },
        {
          name  = "LLM_PROVIDER"
          value = "bedrock"
        },
        {
          name  = "AWS_REGION"
          value = var.aws_region
        },
        {
          name  = "LLM_MODEL_ID"
          value = var.bedrock_model_id
        },
        {
          name  = "REDIS_URL"
          value = "redis://${aws_elasticache_cluster.redis.cache_nodes[0].address}:6379/0"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ai_app.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "analytics_mcp" {
  name            = "${local.name}-analytics-mcp"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.analytics_mcp.arn
  desired_count   = var.analytics_mcp_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = true
  }

  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.main.arn
    service {
      client_alias {
        port     = 8001
        dns_name = "analytics-mcp"
      }
      port_name      = "analytics-mcp"
      discovery_name = "analytics-mcp"
    }
  }
}

resource "aws_ecs_service" "ai_app" {
  name            = "${local.name}-ai-app"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.ai_app.arn
  desired_count   = var.ai_app_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.ai_app.arn
    container_name   = "ai-app"
    container_port   = 8080
  }

  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.main.arn
  }

  depends_on = [aws_lb_listener.http]
}
