resource "aws_ecr_repository" "ai_app" {
  name                 = "${local.name}-ai-app"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "analytics_mcp" {
  name                 = "${local.name}-analytics-mcp"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}
