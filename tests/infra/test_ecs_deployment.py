from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_alb_configuration():
    alb_file = REPO_ROOT / "infra" / "terraform" / "alb.tf"
    assert alb_file.exists(), f"Missing {alb_file}"
    content = alb_file.read_text(encoding="utf-8")

    assert 'resource "aws_lb" "main"' in content
    assert 'resource "aws_security_group" "alb"' in content
    assert 'resource "aws_lb_target_group" "ai_app"' in content
    assert 'resource "aws_lb_listener" "http"' in content
    assert 'path                = "/health"' in content
    assert "port        = 8080" in content


def test_ecs_services_and_task_definitions():
    ecs_file = REPO_ROOT / "infra" / "terraform" / "ecs.tf"
    assert ecs_file.exists(), f"Missing {ecs_file}"
    content = ecs_file.read_text(encoding="utf-8")

    assert 'resource "aws_service_discovery_http_namespace" "main"' in content
    assert 'resource "aws_ecs_task_definition" "ai_app"' in content
    assert 'resource "aws_ecs_task_definition" "analytics_mcp"' in content
    assert 'resource "aws_ecs_service" "ai_app"' in content
    assert 'resource "aws_ecs_service" "analytics_mcp"' in content

    # Check Service Connect configuration
    assert "service_connect_configuration" in content
    assert "analytics-mcp" in content

    # Check container ports
    assert "8080" in content
    assert "8001" in content


def test_network_security_group_rules():
    network_file = REPO_ROOT / "infra" / "terraform" / "network.tf"
    assert network_file.exists(), f"Missing {network_file}"
    content = network_file.read_text(encoding="utf-8")

    # ai-app ingress from ALB
    assert "from_port       = 8080" in content
    assert "security_groups = [aws_security_group.alb.id]" in content

    # analytics-mcp ingress from Service Connect / self
    assert "from_port   = 8001" in content
    assert "self        = true" in content


def test_outputs_contain_alb_and_ecs_names():
    outputs_file = REPO_ROOT / "infra" / "terraform" / "outputs.tf"
    assert outputs_file.exists(), f"Missing {outputs_file}"
    content = outputs_file.read_text(encoding="utf-8")

    assert 'output "alb_dns_name"' in content
    assert 'output "alb_arn"' in content
    assert 'output "ai_app_service_name"' in content
    assert 'output "analytics_mcp_service_name"' in content
    assert 'output "service_connect_namespace"' in content
