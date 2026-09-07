# Keep existing resource identities when introducing the runtime switch.
variable "demo_enabled" {
  description = "Run the paid demo backend. False parks ECS, ALB and Redis while retaining durable data and static hosting."
  type        = bool
  default     = true
}

moved {
  from = aws_lb.main
  to   = aws_lb.main[0]
}

moved {
  from = aws_lb_listener.http
  to   = aws_lb_listener.http[0]
}

moved {
  from = aws_elasticache_cluster.redis
  to   = aws_elasticache_cluster.redis[0]
}

moved {
  from = aws_ecs_task_definition.ai_app
  to   = aws_ecs_task_definition.ai_app[0]
}

moved {
  from = aws_ecs_service.ai_app
  to   = aws_ecs_service.ai_app[0]
}

moved {
  from = aws_ecs_service.analytics_mcp
  to   = aws_ecs_service.analytics_mcp[0]
}

resource "aws_cloudfront_function" "demo_offline" {
  name    = "${local.name}-demo-offline"
  runtime = "cloudfront-js-2.0"
  publish = true
  code    = <<-JS
    function handler(event) {
      return {
        statusCode: 503,
        statusDescription: 'Service Unavailable',
        headers: {
          'content-type': {value: 'application/json'},
          'cache-control': {value: 'no-store'}
        },
        body: JSON.stringify({detail: 'Demo is offline. Please try again after the backend is started.'})
      };
    }
  JS
}

output "demo_enabled" {
  description = "Whether the paid demo backend is enabled."
  value       = var.demo_enabled
}
