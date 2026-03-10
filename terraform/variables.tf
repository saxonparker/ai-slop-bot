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

variable "text_backend" {
  type    = string
  default = "anthropic"
}

variable "image_backend" {
  type    = string
  default = "gemini"
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
