# ── IAM Roles ────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# Dispatch Lambda role: CloudWatch Logs + SNS publish
resource "aws_iam_role" "dispatch" {
  name               = "ai-slop-dispatch-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy_attachment" "dispatch_logs" {
  role       = aws_iam_role.dispatch.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "dispatch_sns" {
  name = "sns-publish"
  role = aws_iam_role.dispatch.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sns:Publish"
      Resource = aws_sns_topic.ai_slop.arn
    }]
  })
}

# Bot Lambda role: CloudWatch Logs + S3 write
resource "aws_iam_role" "bot" {
  name               = "ai-slop-bot-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy_attachment" "bot_logs" {
  role       = aws_iam_role.bot.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "bot_s3" {
  name = "s3-upload"
  role = aws_iam_role.bot.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "s3:PutObject"
      Resource = "arn:aws:s3:::dallepics/dalle/*"
    }]
  })
}

resource "aws_iam_role_policy" "bot_dynamodb" {
  name = "usage-tracking"
  role = aws_iam_role.bot.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:PutItem", "dynamodb:Query"]
      Resource = aws_dynamodb_table.usage.arn
    }]
  })
}

# ── Lambda Functions ─────────────────────────────────────────────────────────

resource "aws_lambda_function" "dispatch" {
  function_name    = "ai-slop-dispatch"
  role             = aws_iam_role.dispatch.arn
  handler          = "ai_slop_dispatch.dispatch"
  runtime          = "python3.12"
  timeout          = 10
  memory_size      = 128
  filename         = var.dispatch_zip_path
  source_code_hash = filebase64sha256(var.dispatch_zip_path)

  environment {
    variables = {
      AI_SLOP_SNS_TOPIC = aws_sns_topic.ai_slop.arn
    }
  }
}

resource "aws_lambda_function" "bot" {
  function_name    = "ai-slop-bot"
  role             = aws_iam_role.bot.arn
  handler          = "ai_slop_bot.ai_slop_bot"
  runtime          = "python3.12"
  timeout          = 120
  memory_size      = 512
  filename         = var.bot_zip_path
  source_code_hash = filebase64sha256(var.bot_zip_path)

  environment {
    variables = {
      TEXT_BACKEND         = var.text_backend
      IMAGE_BACKEND        = var.image_backend
      ANTHROPIC_API_KEY    = var.anthropic_api_key
      GOOGLE_API_KEY       = var.google_api_key
      OPENAI_API_KEY       = var.openai_api_key
      OPENAI_ORGANIZATION  = var.openai_organization
      USAGE_TABLE_NAME     = aws_dynamodb_table.usage.name
    }
  }
}
