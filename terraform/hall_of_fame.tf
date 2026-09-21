resource "aws_dynamodb_table" "hall_of_fame" {
  name         = "ai-slop-hall-of-fame"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "media_key"

  attribute {
    name = "media_key"
    type = "S"
  }
}

resource "aws_iam_role_policy" "hall_of_fame" {
  for_each = { dispatch = aws_iam_role.dispatch.id, bot = aws_iam_role.bot.id }
  name     = "hall-of-fame"
  role     = each.value
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:Scan", "dynamodb:PutItem", "dynamodb:DeleteItem"]
      Resource = aws_dynamodb_table.hall_of_fame.arn
      }, {
      Effect   = "Allow"
      Action   = "s3:GetObject"
      Resource = "arn:aws:s3:::dallepics/dalle/*"
    }]
  })
}

# Resolve Slack's native video file posts without visible curation controls.
resource "aws_dynamodb_table" "gallery_media" {
  name         = "ai-slop-gallery-media"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "slack_file_id"

  attribute {
    name = "slack_file_id"
    type = "S"
  }
}

resource "aws_iam_role_policy" "gallery_media" {
  name = "gallery-media"
  role = aws_iam_role.bot.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:GetItem", "dynamodb:PutItem"]
      Resource = aws_dynamodb_table.gallery_media.arn
    }]
  })
}

resource "aws_apigatewayv2_route" "hall_of_fame" {
  for_each  = toset(["GET", "PUT"])
  api_id    = aws_apigatewayv2_api.ai_slop.id
  route_key = "${each.value} /gallery/hall-of-fame"
  target    = "integrations/${aws_apigatewayv2_integration.dispatch.id}"
}

output "gallery_config" {
  description = "Public configuration deployed beside gallery/index.html"
  value = {
    hallOfFameUrl = "${aws_apigatewayv2_api.ai_slop.api_endpoint}/gallery/hall-of-fame"
  }
}
