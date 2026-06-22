# ai-slop

Unified Slack AI command (`/ai-slop`) with pluggable provider backends.

## Usage

- `/ai-slop <prompt>` — text response (default: Gemini)
- `/ai-slop -i <prompt>` — image generation (default: Grok)
- `/ai-slop -v <prompt>` — video generation (default: xAI Grok)
- `/ai-slop -i --edit <image-url> <prompt>` — image generation/editing with a reference image
- `/ai-slop -i --ref <image-url> <prompt>` — image generation with a style/content reference
- `/ai-slop -v --start <image-url> <prompt>` — video generation from a starting image
- `/ai-slop -v --ref <image-url> <prompt>` — video generation with a loose reference image
- `/ai-slop -v --edit-video <video-url> <prompt>` — edit an existing video (Grok only)
- `/ai-slop -v --extend-video <video-url> <prompt>` — extend a video from its last frame (Grok only)
- `/ai-slop -i --upload` or `/ai-slop -i --edit` — open a Slack upload modal for temporary image references
- `/ai-slop -v --upload` — open a Slack upload modal for temporary video references
- `/ai-slop -e <prompt>` — emoji-only text response
- `/ai-slop -c <prompt>` — start a multi-turn text conversation in a thread
- `@slop-bot <prompt>` (in a conversation thread) — continue the conversation
- `/ai-slop -b gemini <prompt>` — text with specific backend
- `/ai-slop -i -b openai <prompt>` — image with specific backend (DALL-E)
- `/ai-slop -v -b grok <prompt>` — video with specific backend
- `/ai-slop -v -b gemini <prompt>` — video with Veo (native audio/dialogue)
- `[hidden directive]` syntax hides instructions from display

Slack does not allow slash commands inside threads, so conversation
follow-ups are made by `@`-mentioning the bot in the thread instead.

### Reference images and videos

There are two ways to provide reference media for generated content:

- Use URL flags directly in the slash command:
  - `--edit <image-url>` for image edits.
  - `--ref <image-url>` for image or video style/content references.
  - `--start <image-url>` for a video start frame.
  - `--edit-video <video-url>` for Grok video edits from an existing video.
  - `--extend-video <video-url>` for Grok video extensions from the source video's last frame.
- Use the Slack upload modal:
  - `/ai-slop -i --upload` opens an image prompt form with 1-3 uploaded references.
  - `/ai-slop -i --edit` opens the same form for editing an uploaded image.
  - `/ai-slop -i --edit make this watercolor` opens the form with the prompt pre-filled.
  - `/ai-slop -v --upload` opens a video prompt form where uploads can be a single start frame or loose references.

Uploaded modal files are temporary. Slack stores them briefly, then the bot
downloads and deletes them after it has normalized the images for provider
calls. Generated outputs still use the existing S3/CloudFront upload path.

Backend support differs slightly:

- Grok image and video support reference images.
- Grok video supports `--edit-video` and `--extend-video` source-video operations.
- Gemini image supports reference images.
- Gemini video supports one start image, but not loose references or video edit/extend operations.
- OpenAI image uses the edit model when references are supplied.
- Video edit/extend flags are Grok-only; use `-b grok` if `VIDEO_BACKEND` is not `grok`.

## Architecture

Two-Lambda architecture:
1. **Dispatch Lambda** (`ai_slop_dispatch/`) — receives Slack webhook, publishes to SNS
2. **Bot Lambda** (`ai_slop_bot/`) — processes command, calls AI backend, posts result to Slack

## Backends

| Type  | Backend    | Provider                          | Default |
|-------|------------|-----------------------------------|---------|
| Text  | anthropic  | Claude (claude-sonnet-4-6)        |         |
| Text  | gemini     | Gemini (gemini-3.5-flash)         | Yes     |
| Text  | openai     | ChatGPT (gpt-5.5)                 |         |
| Text  | grok       | Grok (grok-4-1-fast)              |         |
| Image | gemini     | Nano Banana 2 (gemini-3.1-flash-image) |    |
| Image | openai     | DALL-E 3                          |         |
| Image | grok       | Grok Imagine (quality)            | Yes     |
| Video | grok       | Grok Imagine Video                | Yes     |
| Video | gemini     | Veo 3.1 Fast (with audio)         |         |

## Environment Variables

### Bot Lambda
| Variable | Default | Purpose |
|---|---|---|
| `TEXT_BACKEND` | `gemini` | Default text provider |
| `IMAGE_BACKEND` | `grok` | Default image provider |
| `VIDEO_BACKEND` | `grok` | Default video provider |
| `TEXT_MODEL` | varies by backend | Model name override |
| `IMAGE_MODEL` | varies by backend | Model name override |
| `VIDEO_MODEL` | varies by backend (`grok-imagine-video` / `veo-3.1-fast-generate-preview`) | Model name override |
| `ANTHROPIC_API_KEY` | — | Required if using anthropic backend |
| `GOOGLE_API_KEY` | — | Required if using gemini backends |
| `GROK_IMAGE_EDIT_TIMEOUT_SECONDS` | `180` | Timeout for Grok image edit requests |
| `OPENAI_API_KEY` | — | Required if using openai backends |
| `OPENAI_IMAGE_EDIT_MODEL` | `gpt-image-2` | OpenAI model used when reference images are supplied |
| `OPENAI_ORGANIZATION` | — | Required if using openai backends |
| `XAI_API_KEY` | — | Required if using grok backends |

### Dispatch Lambda
| Variable | Purpose |
|---|---|
| `AI_SLOP_SNS_TOPIC` | SNS topic ARN |
| `SLACK_BOT_TOKEN` | Opens Slack upload modals |

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
   - `SLACK_BOT_TOKEN`
   - `XAI_API_KEY`

3. Push to `main` — GitHub Actions will test, build, and deploy.

4. After first deploy, get the API Gateway base URL:
   ```bash
   cd terraform && terraform output api_gateway_url
   ```

   Configure your Slack app at https://api.slack.com/apps as follows:
   - **Slash command** Request URL: `<base_url>/ai-slop`
     - Usage Hint:
       ```text
       <prompt> | -i | -v [sec] | --upload | --edit [img-url] | --ref/--start <img-url> | --edit-video/--extend-video <video-url> | -b <backend> | -e | -p | -u | -pay <amt>
       ```
   - **Interactivity & Shortcuts** → Enable Interactivity
     - Request URL: `<base_url>/slack/interactions`
   - **Event Subscriptions** → Enable Events
     - Request URL: `<base_url>/slack/events`
     - Subscribe to bot event: `app_mention`
   - **OAuth & Permissions** → Bot Token Scopes:
     - `chat:write` - write messages
     - `commands` - receive slash commands
     - `files:read` - read uploaded reference images
     - `files:write` - upload generated videos and delete temporary reference images
     - `app_mentions:read` — receive `@slop-bot` events
     - `users:read` — resolve user IDs to display names in transcripts
   - Reinstall the app to your workspace after changing scopes; copy the
     new Bot User OAuth Token into the `slack_bot_token` Terraform variable.
   - Invite the bot to any channel where users will `@`-mention it
     (`/invite @slop-bot`).

   Note: neither endpoint currently verifies Slack request signatures —
   adding `X-Slack-Signature` verification with `SLACK_SIGNING_SECRET` is
   a follow-up.

### Manual deploy

```bash
cd ai_slop_bot && make
cd ai_slop_dispatch && make
cd terraform && terraform init && terraform apply
```
