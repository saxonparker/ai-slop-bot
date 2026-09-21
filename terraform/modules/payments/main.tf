variable "environment" {
  type = string
  validation {
    condition     = contains(["sandbox", "live"], var.environment)
    error_message = "Payments require an explicit sandbox or live environment."
  }
}

variable "api_id" { type = string }
variable "api_endpoint" { type = string }
variable "api_execution_arn" { type = string }
variable "ledger_name" { type = string }
variable "ledger_arn" { type = string }
variable "zip_path" { type = string }
variable "enabled" { type = bool }
variable "client_id" {
  type      = string
  sensitive = true
}
variable "client_secret" {
  type      = string
  sensitive = true
}
variable "webhook_id" {
  type      = string
  sensitive = true
}

locals {
  name     = "ai-slop-payments-${var.environment}"
  base_url = "${var.api_endpoint}/payments/${var.environment}"
}

resource "aws_dynamodb_table" "payments" {
  name         = local.name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"
  attribute {
    name = "id"
    type = "S"
  }
  # No TTL: payment/capture identifiers must survive webhook replays.
}

resource "aws_iam_role" "payments" {
  name = "${local.name}-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Action = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.payments.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "ledger" {
  name = "payment-ledger"
  role = aws_iam_role.payments.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.payments.arn
      },
      {
        Effect = "Allow", Action = "dynamodb:PutItem", Resource = var.ledger_arn
      },
    ]
  })
}

resource "aws_lambda_function" "payments" {
  function_name    = local.name
  role             = aws_iam_role.payments.arn
  handler          = "payment_handler.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = var.zip_path
  source_code_hash = filebase64sha256(var.zip_path)
  environment {
    variables = {
      PAYPAL_ENVIRONMENT         = var.environment
      PAYPAL_CLIENT_ID           = var.client_id
      PAYPAL_CLIENT_SECRET       = var.client_secret
      PAYPAL_WEBHOOK_ID          = var.webhook_id
      PAYMENTS_TABLE_NAME        = aws_dynamodb_table.payments.name
      PAYMENTS_LEDGER_TABLE_NAME = var.ledger_name
      PAYMENTS_BASE_URL          = local.base_url
      PAYMENTS_ENABLED           = tostring(var.enabled)
    }
  }
  lifecycle {
    precondition {
      condition     = var.environment != "sandbox" || endswith(var.ledger_name, "-sandbox")
      error_message = "Sandbox must write only to its sandbox ledger."
    }
    precondition {
      condition     = !var.enabled || (var.client_id != "" && var.client_secret != "" && var.webhook_id != "")
      error_message = "Enabling purchases requires PayPal credentials and a registered webhook ID."
    }
  }
}

resource "aws_apigatewayv2_integration" "payments" {
  api_id                 = var.api_id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.payments.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "payments" {
  for_each  = toset(["GET checkout", "POST status", "POST order", "POST capture", "POST webhook"])
  api_id    = var.api_id
  route_key = "${split(" ", each.value)[0]} /payments/${var.environment}/${split(" ", each.value)[1]}"
  target    = "integrations/${aws_apigatewayv2_integration.payments.id}"
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowPaymentAPI"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.payments.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${var.api_execution_arn}/*/*/payments/${var.environment}/*"
}

output "table_name" { value = aws_dynamodb_table.payments.name }
output "table_arn" { value = aws_dynamodb_table.payments.arn }
output "base_url" { value = local.base_url }
output "webhook_url" { value = "${local.base_url}/webhook" }
output "function_name" { value = aws_lambda_function.payments.function_name }
