variable "paypal_client_id" {
  type      = string
  sensitive = true
  default   = ""
}
variable "paypal_client_secret" {
  type      = string
  sensitive = true
  default   = ""
}
variable "paypal_webhook_id" {
  type      = string
  sensitive = true
  default   = ""
}
variable "paypal_live_enabled" {
  type        = bool
  default     = false
  description = "Switch -pay from the existing Venmo flow to verified live PayPal purchases after sandbox validation."
}
variable "slack_signing_secret" {
  type      = string
  sensitive = true
  default   = ""
}

module "payments" {
  source            = "./modules/payments"
  environment       = "live"
  api_id            = aws_apigatewayv2_api.ai_slop.id
  api_endpoint      = aws_apigatewayv2_api.ai_slop.api_endpoint
  api_execution_arn = aws_apigatewayv2_api.ai_slop.execution_arn
  ledger_name       = aws_dynamodb_table.ledger.name
  ledger_arn        = aws_dynamodb_table.ledger.arn
  zip_path          = var.bot_zip_path
  client_id         = var.paypal_client_id
  client_secret     = var.paypal_client_secret
  webhook_id        = var.paypal_webhook_id
  enabled           = var.paypal_live_enabled
}

resource "aws_iam_role_policy" "bot_payments" {
  name = "create-pending-checkout"
  role = aws_iam_role.bot.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Action = "dynamodb:PutItem", Resource = module.payments.table_arn
    }]
  })
}

output "paypal_webhook_url" { value = module.payments.webhook_url }

data "aws_caller_identity" "payments" {}
data "aws_region" "payments" {}
data "aws_partition" "payments" {}

resource "aws_iam_role_policy" "bot_sandbox_checkout" {
  name = "create-sandbox-checkout"
  role = aws_iam_role.bot.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow", Action = "lambda:InvokeFunction"
      Resource = "arn:${data.aws_partition.payments.partition}:lambda:${data.aws_region.payments.name}:${data.aws_caller_identity.payments.account_id}:function:ai-slop-payments-sandbox"
    }]
  })
}
