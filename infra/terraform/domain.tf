# Custom Domain & SSL Certificate for CloudFront (sibkaro.com and ai.sibkaro.com)

data "aws_route53_zone" "primary" {
  count        = var.enable_custom_domain ? 1 : 0
  name         = "${var.custom_domain_name}."
  private_zone = false
}

# 1. Request public ACM Certificate in us-east-1 with DNS validation
resource "aws_acm_certificate" "cert" {
  count                     = var.enable_custom_domain ? 1 : 0
  domain_name               = var.custom_domain_name
  subject_alternative_names = [var.custom_subdomain_name, "*.${var.custom_domain_name}"]
  validation_method         = "DNS"

  tags = {
    Name = "${local.name}-acm-cert"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# 2. Automated Route 53 DNS validation records
resource "aws_route53_record" "cert_validation" {
  for_each = var.enable_custom_domain ? {
    for dvo in tolist(aws_acm_certificate.cert[0].domain_validation_options) : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  allow_overwrite = true
  name            = each.value.name
  records         = [each.value.record]
  ttl             = 60
  type            = each.value.type
  zone_id         = data.aws_route53_zone.primary[0].zone_id
}

# 3. Wait for DNS certificate validation to complete
resource "aws_acm_certificate_validation" "cert" {
  count                   = var.enable_custom_domain ? 1 : 0
  certificate_arn         = aws_acm_certificate.cert[0].arn
  validation_record_fqdns = [for record in aws_route53_record.cert_validation : record.fqdn]
}

# 4. Route 53 A and AAAA Alias Records for Subdomain (ai.sibkaro.com)
resource "aws_route53_record" "subdomain_a" {
  count   = var.enable_custom_domain ? 1 : 0
  zone_id = data.aws_route53_zone.primary[0].zone_id
  name    = var.custom_subdomain_name
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.main.domain_name
    zone_id                = aws_cloudfront_distribution.main.hosted_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "subdomain_aaaa" {
  count   = var.enable_custom_domain ? 1 : 0
  zone_id = data.aws_route53_zone.primary[0].zone_id
  name    = var.custom_subdomain_name
  type    = "AAAA"

  alias {
    name                   = aws_cloudfront_distribution.main.domain_name
    zone_id                = aws_cloudfront_distribution.main.hosted_zone_id
    evaluate_target_health = false
  }
}
