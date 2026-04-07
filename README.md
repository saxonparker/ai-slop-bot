# ai-slop

Unified Slack AI command (`/ai-slop`) with pluggable provider backends.

## Usage

- `/ai-slop <prompt>` — text response (default: Anthropic Claude)
- `/ai-slop -i <prompt>` — image generation (default: Google Gemini)
- `/ai-slop -v <prompt>` — video generation (default: xAI Grok)
- `/ai-slop -e <prompt>` — emoji-only text response
- `/ai-slop -b gemini <prompt>` — text with specific backend
- `/ai-slop -i -b openai <prompt>` — image with specific backend (DALL-E)
- `/ai-slop -v -b grok <prompt>` — video with specific backend
- `[hidden directive]` syntax hides instructions from display

## Architecture

Two-Lambda architecture:
1. **Dispatch Lambda** (`ai_slop_dispatch/`) — receives Slack webhook, publishes to SNS
2. **Bot Lambda** (`ai_slop_bot/`) — processes command, calls AI backend, posts result to Slack

## Backends

| Type  | Backend    | Provider                    | Default |
|-------|------------|-----------------------------|---------|
| Text  | anthropic  | Claude                      | Yes     |
| Text  | gemini     | Gemini                      |         |
| Text  | openai     | ChatGPT                     |         |
| Text  | grok       | Grok (grok-4-1-fast)        |         |
| Image | gemini     | Nano Banana                 | Yes     |
| Image | openai     | DALL-E 3                    |         |
| Image | grok       | Grok Imagine                |         |
| Video | grok       | Grok Imagine Video          | Yes     |

## Environment Variables

### Bot Lambda
| Variable | Default | Purpose |
|---|---|---|
| `TEXT_BACKEND` | `gemini` | Default text provider |
| `IMAGE_BACKEND` | `grok` | Default image provider |
| `VIDEO_BACKEND` | `grok` | Default video provider |
| `TEXT_MODEL` | varies by backend | Model name override |
| `IMAGE_MODEL` | `gemini-3.1-flash-image-preview` | Model name override |
| `VIDEO_MODEL` | `grok-imagine-video` | Model name override |
| `ANTHROPIC_API_KEY` | — | Required if using anthropic backend |
| `GOOGLE_API_KEY` | — | Required if using gemini backends |
| `OPENAI_API_KEY` | — | Required if using openai backends |
| `OPENAI_ORGANIZATION` | — | Required if using openai backends |
| `XAI_API_KEY` | — | Required if using grok backends |

### Dispatch Lambda
| Variable | Purpose |
|---|---|
| `AI_SLOP_SNS_TOPIC` | SNS topic ARN |

## Build

```bash
cd ai_slop_bot && make
cd ai_slop_dispatch && make
```

## Test

```bash
cd ai_slop_bot && make check
```

## Deployment

Infrastructure is managed with Terraform. CI/CD runs via GitHub Actions on push to `main`.

### First-time setup

1. Create the Terraform state bucket:
   ```bash
   aws s3api create-bucket --bucket terraform-state-ai-slop --region us-east-2 \
     --create-bucket-configuration LocationConstraint=us-east-2
   ```

2. Add these GitHub Actions secrets:
   - `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
   - `ANTHROPIC_API_KEY`
   - `GOOGLE_API_KEY`
   - `OPENAI_API_KEY`
   - `OPENAI_ORGANIZATION`
   - `XAI_API_KEY`

3. Push to `main` — GitHub Actions will test, build, and deploy.

4. After first deploy, get the Slack webhook URL:
   ```bash
   cd terraform && terraform output api_gateway_url
   ```
   Configure this as the Request URL in your Slack slash command settings.

### Manual deploy

```bash
cd ai_slop_bot && make
cd ai_slop_dispatch && make
cd terraform && terraform init && terraform apply
```
