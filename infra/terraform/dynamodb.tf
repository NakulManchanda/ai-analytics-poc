# The durable application-state schema is introduced in Milestone 7. This
# foundation supplies one on-demand table with the stable key shape it will use.
resource "aws_dynamodb_table" "application_state" {
  name         = "${local.name}-application-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }
}
