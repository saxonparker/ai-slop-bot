resource "aws_apigatewayv2_api" "ai_slop" {
  name          = "ai-slop-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_stage" "prod" {
  api_id      = aws_apigatewayv2_api.ai_slop.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_apigatewayv2_integration" "dispatch" {
  api_id                 = aws_apigatewayv2_api.ai_slop.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.dispatch.invoke_arn
  payload_format_version = "1.0"
}

resource "aws_apigatewayv2_route" "post_ai_slop" {
  api_id    = aws_apigatewayv2_api.ai_slop.id
  route_key = "POST /ai-slop"
  target    = "integrations/${aws_apigatewayv2_integration.dispatch.id}"
}

resource "aws_apigatewayv2_route" "post_slack_events" {
  api_id    = aws_apigatewayv2_api.ai_slop.id
  route_key = "POST /slack/events"
  target    = "integrations/${aws_apigatewayv2_integration.dispatch.id}"
}

resource "aws_lambda_permission" "apigw_invoke_dispatch" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.dispatch.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.ai_slop.execution_arn}/*/*"
}
