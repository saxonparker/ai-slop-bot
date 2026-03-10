# Reference existing S3 bucket — not managed by Terraform.
# The bucket and CloudFront distribution were created manually
# and are shared with the older dalle_slack project.

data "aws_s3_bucket" "dallepics" {
  bucket = "dallepics"
}
