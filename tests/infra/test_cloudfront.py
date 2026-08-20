from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_cloudfront_distribution_configuration():
    cf_file = REPO_ROOT / "infra" / "terraform" / "cloudfront.tf"
    assert cf_file.exists(), f"Missing {cf_file}"
    content = cf_file.read_text(encoding="utf-8")

    assert 'resource "aws_cloudfront_origin_access_control" "frontend"' in content
    assert 'resource "aws_s3_bucket_policy" "frontend"' in content
    assert 'resource "aws_cloudfront_distribution" "main"' in content

    # Check S3 origin and ALB custom origin
    assert "bucket_regional_domain_name" in content
    assert "origin_access_control_id" in content
    assert "custom_origin_config" in content

    # Check caching behaviors
    assert "default_cache_behavior" in content
    assert 'path_pattern           = "/api/*"' in content
    assert "redirect-to-https" in content


def test_cloudfront_outputs():
    outputs_file = REPO_ROOT / "infra" / "terraform" / "outputs.tf"
    assert outputs_file.exists(), f"Missing {outputs_file}"
    content = outputs_file.read_text(encoding="utf-8")

    assert 'output "cloudfront_domain_name"' in content
    assert 'output "cloudfront_distribution_id"' in content
    assert 'output "cloudfront_url"' in content
