resource "aws_dynamodb_table" "usage" {
  name         = "ai-slop-usage"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user"
  range_key    = "timestamp"

  attribute {
    name = "user"
    type = "S"
  }

  attribute {
    name = "timestamp"
    type = "S"
  }
}

resource "aws_dynamodb_table" "ledger" {
  name         = "ai-slop-ledger"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user"
  range_key    = "timestamp"

  attribute {
    name = "user"
    type = "S"
  }

  attribute {
    name = "timestamp"
    type = "S"
  }
}
