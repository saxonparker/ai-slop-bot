resource "aws_sns_topic" "ai_slop" {
  name = "ai-slop-topic"
}

resource "aws_sns_topic_subscription" "bot" {
  topic_arn = aws_sns_topic.ai_slop.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.bot.arn
}

resource "aws_lambda_permission" "sns_invoke_bot" {
  statement_id  = "AllowSNSInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.bot.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.ai_slop.arn
}
