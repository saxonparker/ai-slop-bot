variable "anthropic_api_key" {
  type      = string
  sensitive = true
}

variable "google_api_key" {
  type      = string
  sensitive = true
}

variable "openai_api_key" {
  type      = string
  sensitive = true
}

variable "openai_organization" {
  type      = string
  sensitive = true
}

variable "xai_api_key" {
  type      = string
  sensitive = true
}

variable "slack_bot_token" {
  type      = string
  sensitive = true
}

variable "text_backend" {
  type    = string
  default = "gemini"
}

variable "image_backend" {
  type    = string
  default = "gemini"
}

variable "video_backend" {
  type    = string
  default = "grok"
}

variable "bot_zip_path" {
  type        = string
  description = "Path to the ai_slop_bot.zip Lambda package"
  default     = "../ai_slop_bot/ai_slop_bot.zip"
}

variable "dispatch_zip_path" {
  type        = string
  description = "Path to the ai_slop_dispatch.zip Lambda package"
  default     = "../ai_slop_dispatch/ai_slop_dispatch.zip"
}

variable "venmo_username" {
  type    = string
  default = "Saxon-Parker"
}

variable "admin_users" {
  type        = string
  description = "Comma-separated list of Slack usernames allowed to use -credit"
  default     = "saxon"
}
