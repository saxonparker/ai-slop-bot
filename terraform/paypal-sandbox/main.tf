# A separate state, API, ledger and IAM role keep test money out of the bot.
terraform {
  required_version = ">= 1.3"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
  backend "s3" {
    bucket = "terraform-state-ai-slop"
    key    = "ai-slop/paypal-sandbox.tfstate"
    region = "us-east-2"
  }
}

provider "aws" { region = "us-east-2" }

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
  default   = ""
}
variable "zip_path" {
  type    = string
  default = "../../ai_slop_bot/payments.zip"
}

resource "aws_dynamodb_table" "ledger" {
  name         = "ai-slop-ledger-sandbox"
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

resource "aws_apigatewayv2_api" "sandbox" {
  name          = "ai-slop-paypal-sandbox"
  protocol_type = "HTTP"
}
resource "aws_apigatewayv2_stage" "sandbox" {
  api_id      = aws_apigatewayv2_api.sandbox.id
  name        = "$default"
  auto_deploy = true
}

module "payments" {
  source            = "../modules/payments"
  environment       = "sandbox"
  api_id            = aws_apigatewayv2_api.sandbox.id
  api_endpoint      = aws_apigatewayv2_api.sandbox.api_endpoint
  api_execution_arn = aws_apigatewayv2_api.sandbox.execution_arn
  ledger_name       = aws_dynamodb_table.ledger.name
  ledger_arn        = aws_dynamodb_table.ledger.arn
  zip_path          = var.zip_path
  client_id         = var.client_id
  client_secret     = var.client_secret
  webhook_id        = var.webhook_id
  enabled           = var.webhook_id != ""
}

output "webhook_url" { value = module.payments.webhook_url }
output "function_name" { value = module.payments.function_name }
