resource "aws_dynamodb_table" "conversations" {
  name         = "ai-slop-conversations"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "conversation_id"

  attribute {
    name = "conversation_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

resource "aws_iam_role_policy" "conversations" {
  name = "conversations"
  role = aws_iam_role.bot.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
      Resource = aws_dynamodb_table.conversations.arn
    }]
  })
}
