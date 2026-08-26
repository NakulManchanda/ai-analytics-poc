# ElastiCache Redis — transient event and job delivery
#
# Single-node, no cluster mode, no multi-AZ replication. Redis is intentionally
# transient coordination only; DynamoDB remains the durable truth for all
# conversation and job state. Production tasks must not assume localhost Redis.

resource "aws_elasticache_subnet_group" "redis" {
  name        = "${local.name}-redis"
  description = "Private subnet group for ElastiCache Redis (${local.name})"
  subnet_ids  = aws_subnet.private[*].id

  tags = merge(local.common_tags, {
    Name = "${local.name}-redis-subnet-group"
  })
}

resource "aws_security_group" "redis" {
  name        = "${local.name}-redis"
  description = "ElastiCache Redis: allow inbound 6379 from ECS tasks only"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Redis from ECS app and worker tasks"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_tasks.id]
  }

  egress {
    description = "No egress required for a cache node"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["127.0.0.1/32"]
  }

  tags = merge(local.common_tags, {
    Name = "${local.name}-redis"
  })
}

resource "aws_elasticache_cluster" "redis" {
  cluster_id           = "${local.name}-redis"
  engine               = "redis"
  engine_version       = var.redis_engine_version
  node_type            = var.redis_node_type
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  port                 = 6379
  subnet_group_name    = aws_elasticache_subnet_group.redis.name
  security_group_ids   = [aws_security_group.redis.id]

  # Disable persistence — Redis is transient event/job delivery only.
  snapshot_retention_limit = 0

  apply_immediately = true

  tags = merge(local.common_tags, {
    Name = "${local.name}-redis"
  })
}
