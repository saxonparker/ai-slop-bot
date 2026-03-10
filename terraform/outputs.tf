output "api_gateway_url" {
  description = "URL to configure as the Slack slash command Request URL"
  value       = "${aws_apigatewayv2_api.ai_slop.api_endpoint}/ai-slop"
}

output "sns_topic_arn" {
  description = "SNS topic ARN connecting dispatch to bot"
  value       = aws_sns_topic.ai_slop.arn
}
