# ai-slop

Unified Slack AI command (`/slop-bot`) with pluggable provider backends.

## Usage

The user-facing Slack slash command is `/slop-bot`. The Slack app still posts
slash-command payloads to the `/ai-slop` HTTP route during deployment.

- `/slop-bot <prompt>` — text response (default: Gemini)
- `/slop-bot -i <prompt>` — image generation (default: Grok)
- `/slop-bot -v [seconds] <prompt>` — video generation (default: Grok)
- `/slop-bot -e <prompt>` — emoji-only text response
- `/slop-bot -p <prompt>` — potato mode
- `/slop-bot -c <prompt>` or `/slop-bot --conversation <prompt>` — start a multi-turn text conversation in a thread
- `@slop-bot <prompt>` (in a conversation thread) — continue the conversation
- `/slop-bot -b gemini <prompt>` — text with a specific backend
- `/slop-bot -i -b openai <prompt>` — image with a specific backend
- `/slop-bot -v -b grok <prompt>` — video with a specific backend
- `/slop-bot -v -b gemini <prompt>` — video with Veo (native audio/dialogue)

Slack does not allow slash commands inside threads, so conversation
follow-ups are made by `@`-mentioning the bot in the thread instead.

### Flags

Flags can appear in any order unless a flag consumes the next value.

- `-i` — image generation.
- `-v [seconds]` — video generation. Grok defaults to 10 seconds and supports up to 15 seconds; Grok reference-to-video supports up to 10 seconds. Veo (`-b gemini`) supports 4, 6, or 8 seconds and snaps other requested durations to the nearest supported value.
- `-e` — emoji-only response.
- `-p` — potato mode.
- `-c`, `--conversation` — start a text-only conversation in a Slack thread.
- `-b <backend>` — override the backend for the selected mode.
- `-u`, `--usage` — show your usage stats and credit balance.
- `-g`, `--gallery` — show the AI Slop Gallery link.
- `-pay <amount>`, `--pay <amount>` — add credits and receive a Venmo payment link.
- `--upload` — open the Slack upload modal; combine with `-i` or `-v`.
- `--edit <image-url>` — edit an image from a URL; with `-i --edit` and no URL, open the upload modal for an uploaded image edit.
- `--ref <image-url>` — add an image reference. Repeat for multiple references.
- `--start <image-url>` — use an image URL as the start frame for a video.
- `--edit-video <video-url>` — edit an existing video (Grok only).
- `--extend-video <video-url>` — extend a video from its last frame (Grok only).
- `--report` (`-report` is also accepted) — admin-only balance report; the caller must be listed in `ADMIN_USERS`.
- `--credit <user> <amount>` (`-credit` is also accepted) — admin-only credit adjustment; the caller must be listed in `ADMIN_USERS`, and the amount can be negative.

### Bracket syntax

- `[hidden directive]` — included in the AI prompt but removed from the visible Slack prompt.
  Example: `/slop-bot tell me a joke [make it about dogs]`.
- `]shown text[` — shown in Slack but removed from the AI prompt.
  Example: `/slop-bot what's the capital of France? ]asking for a friend[`.

### Reference images and videos

There are two ways to provide reference media for generated content:

- Use URL flags directly in the slash command:
  - `--edit <image-url>` for image edits.
  - `--ref <image-url>` for image or video style/content references.
  - `--start <image-url>` for a video start frame.
  - `--edit-video <video-url>` for Grok video edits from an existing video.
  - `--extend-video <video-url>` for Grok video extensions from the source video's last frame.
- Use the Slack upload modal:
  - `/slop-bot -i --upload` opens an image prompt form with 1-3 uploaded references.
  - `/slop-bot -i --edit` opens the same form for editing an uploaded image.
  - `/slop-bot -i --edit make this watercolor` opens the form with the prompt pre-filled.
  - `/slop-bot -v --upload` opens a video prompt form where image uploads can be a single start frame or loose references, and source video uploads can be used for edit/extend.

Uploaded modal files are temporary. Slack stores them briefly, then the bot
downloads and deletes them after it has normalized images or staged source
videos through the existing S3/CloudFront upload path for provider calls.
Generated outputs still use the same S3/CloudFront upload path.

Backend support differs slightly:

- Grok image and video support reference images.
- Grok video supports `--edit-video` and `--extend-video` source-video operations.
- Gemini image supports reference images.
- Gemini video supports one start image, but not loose references or video edit/extend operations.
- OpenAI image uses the edit model when references are supplied.
- Video edit/extend flags are Grok-only; use `-b grok` if `VIDEO_BACKEND` is not `grok`.

## Conversations

`/slop-bot -c <prompt>` starts a text-only conversation by posting the first
answer as a top-level Slack message and storing the transcript against that
thread. Slack slash commands cannot be used inside threads, so follow-up turns
must mention the bot in the thread with `@slop-bot <prompt>`. Conversation mode
cannot be combined with `-i` or `-v`.

Conversations require `CONVERSATIONS_TABLE_NAME`. If that environment variable
is unset, `-c` returns "Conversations are not enabled in this environment." and
thread mentions do not continue history. The DynamoDB row is keyed by
`conversation_id`, composed as `channel_id:thread_ts`, and stores the full
transcript in one item so every turn can replay history to the selected text
backend.

Continuation limits are enforced before provider calls:

- `CONVERSATION_MAX_CHARS` defaults to `200000` total stored characters.
- `ASSISTANT_RESERVE_CHARS` defaults to `16000`, reserved as response headroom
  before accepting another turn.
- `CONVERSATION_MAX_TURNS` defaults to `100` user/assistant turns.
- A warning footer appears after roughly 80% of the character cap.

Each continuation turn takes a per-conversation DynamoDB lock
(`lock_holder`/`lock_expires_at`) before reading and appending history. The bot
retries once after two seconds when the lock is busy; fresh locks expire after
360 seconds. Appends also check the previous `turn_count` so an overlapping turn
cannot corrupt the transcript.

## Budget & Credits

Balances are calculated as:

```text
balance = ledger credits - usage costs
```

Credits live in the DynamoDB ledger table named by `LEDGER_TABLE_NAME`
(default: `ai-slop-ledger`). Usage costs come from `USAGE_TABLE_NAME` (default:
`ai-slop-usage`) and use actual billed cost when available, otherwise the
stored estimate.

`/slop-bot -pay <amount>` records a ledger payment credit for the caller and
returns a Venmo payment link generated from `VENMO_USERNAME` (default:
`Saxon-Parker`). The link is a convenience deep link; the bot records the credit
when the command is accepted and does not verify Venmo settlement.

`/slop-bot -u` shows the caller's usage summary plus their current balance. The
balance display includes the latest ledger entry amount/date when one exists.

Admin budget commands are gated by `ADMIN_USERS`, a comma-separated list of
Slack usernames with no spaces:

- `/slop-bot --report` scans the usage and ledger tables, discovers users, and
  reports each user's balance, total spend, and total credits.
- `/slop-bot --credit <user> <amount>` writes an admin adjustment to the ledger
  and reports the target user's new balance. The amount can be negative.

## Usage Tracking and Audit

Every provider attempt is recorded best-effort in DynamoDB. Successful requests
write `status=succeeded`; failed provider attempts write `status=failed` with
`error_type` and a truncated `error_message`. Usage rows are keyed by
`user`/`timestamp` and include `mode` (`text`, `image`, or `video`), `backend`,
`model`, `cost_estimate`, token counts, and optional actual-cost fields
(`cost_actual` and `cost_in_usd_ticks`) when a provider exposes exact billing.

The in-Slack `-u` / `--usage` summary queries the caller's rows and reports
last 7 days, current month, and all time, broken down by mode with failed counts
included.

Operators can audit usage with:

```bash
cd ai_slop_bot
python audit_usage.py --start-date 2026-06-01 --end-date 2026-06-30
```

`audit_usage.py` scans the usage table and aggregates by UTC date, backend,
mode, status, and model. It supports `--table`, `--start-date`, `--end-date`,
`--backend`, `--mode`, `--status`, `--user`, and `--model` filters. By default
it prints an aligned table; `--json` prints the aggregate summary as JSON, and
`--details-csv <path>` writes matching per-request rows for spreadsheet-level
inspection.

`scrape_logs.py` is a separate operator utility for CloudWatch Logs export. It
queries recent dispatch/bot logs and writes prompt TSVs; it is not the source of
billing or balance data.

## Gallery

`/slop-bot -g` or `/slop-bot --gallery` returns the CloudFront gallery link:
`https://d2jagmvo7k5q5j.cloudfront.net/index.html`.

The static gallery source is `gallery/index.html`. It uses a Cognito-backed S3
client in the browser to list the `dallepics` bucket under the `dalle/` prefix,
renders images and videos through the CloudFront distribution, and reads
`dalle/manifest.json` for user, channel, and model metadata. The page supports
photo/video filtering, prompt search, user/channel filters, pagination, and a
modal viewer.

Generated images and videos uploaded through `image_upload.upload_to_s3()` use
the `dalle/` prefix and update the manifest when user/channel/model metadata is
present. Temporary source videos for edit/extend workflows use the
`source-videos/` prefix and are intentionally excluded from the gallery
manifest.

## Architecture

Two-Lambda architecture:
1. **Dispatch Lambda** (`ai_slop_dispatch/`) — receives Slack webhook, publishes to SNS
2. **Bot Lambda** (`ai_slop_bot/`) — processes command, calls AI backend, posts result to Slack

## Backends

| Type  | Backend    | Default model                     | Default |
|-------|------------|-----------------------------------|---------|
| Text  | anthropic  | `claude-sonnet-4-6`               |         |
| Text  | gemini     | `gemini-3.5-flash`                | Yes     |
| Text  | openai     | `gpt-5.5`                         |         |
| Text  | grok       | `grok-4-1-fast-non-reasoning`     |         |
| Image | gemini     | `gemini-3.1-flash-image`          |         |
| Image | openai     | `dall-e-3`                        |         |
| Image | grok       | `grok-imagine-image-quality`      | Yes     |
| Video | grok       | `grok-imagine-video`              | Yes     |
| Video | gemini     | `veo-3.1-fast-generate-preview`   |         |

## Environment Variables

### Bot Lambda
| Variable | Default | Purpose |
|---|---|---|
| `TEXT_BACKEND` | `gemini` | Default text provider |
| `IMAGE_BACKEND` | `grok` | Default image provider |
| `VIDEO_BACKEND` | `grok` | Default video provider |
| `TEXT_MODEL` | backend default | Text model override for the selected text backend |
| `IMAGE_MODEL` | backend default | Image model override for the selected image backend |
| `VIDEO_MODEL` | backend default | Video model override for the selected video backend |
| `VIDEO_DURATION` | Grok `10`, Gemini `8` | Default video length when `-v` does not include seconds |
| `ANTHROPIC_API_KEY` | — | Required if using anthropic backend |
| `GOOGLE_API_KEY` | — | Required if using gemini backends |
| `GROK_IMAGE_EDIT_TIMEOUT_SECONDS` | `180` | Timeout for Grok image edit requests |
| `OPENAI_API_KEY` | — | Required if using openai backends |
| `OPENAI_IMAGE_EDIT_MODEL` | `gpt-image-2` | OpenAI model used when reference images are supplied |
| `OPENAI_ORGANIZATION` | — | Required if using openai backends |
| `XAI_API_KEY` | — | Required if using grok backends |
| `SLACK_BOT_TOKEN` | — | Slack Web API token for posting responses, uploads, modals, reference downloads, cleanup, and user lookup |
| `USAGE_TABLE_NAME` | `ai-slop-usage` | DynamoDB usage table for request records, usage summaries, balances, and audit CLI |
| `LEDGER_TABLE_NAME` | `ai-slop-ledger` | DynamoDB credit ledger table for payments and admin adjustments |
| `CONVERSATIONS_TABLE_NAME` | unset | DynamoDB conversation table; conversations are disabled when unset |
| `CONVERSATION_MAX_CHARS` | `200000` | Maximum stored transcript characters per conversation |
| `ASSISTANT_RESERVE_CHARS` | `16000` | Reserved transcript headroom before accepting a continuation turn |
| `CONVERSATION_MAX_TURNS` | `100` | Maximum user/assistant turns per conversation |
| `VENMO_USERNAME` | `Saxon-Parker` | Venmo username used in generated payment links |
| `ADMIN_USERS` | `saxon` | Comma-separated Slack usernames allowed to use budget admin commands |
| `REFERENCE_IMAGE_MAX_BYTES` | `20971520` | Maximum reference image size before normalization |
| `REFERENCE_IMAGE_MAX_EDGE` | `2048` | Maximum reference image width or height after normalization |
| `REFERENCE_VIDEO_MAX_BYTES` | `209715200` | Maximum uploaded source video size |

### Dispatch Lambda
| Variable | Purpose |
|---|---|
| `AI_SLOP_SNS_TOPIC` | SNS topic ARN |
| `SLACK_BOT_TOKEN` | Slack Web API token for opening and updating upload modals |

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
       /slop-bot <prompt> | -i | -v [sec] | -c/--conversation | -g/--gallery | --upload | --edit [img-url] | --ref/--start <img-url> | --edit-video/--extend-video <video-url> | -b <backend> | -e | -p | -u | -pay <amt>
       ```
   - **Interactivity & Shortcuts** → Enable Interactivity
     - Request URL: `<base_url>/slack/interactions`
   - **Event Subscriptions** → Enable Events
     - Request URL: `<base_url>/slack/events`
     - Subscribe to bot event: `app_mention`
   - **OAuth & Permissions** → Bot Token Scopes:
     - `chat:write` - write messages
     - `commands` - receive slash commands
     - `files:read` - read uploaded reference images and source videos
     - `files:write` - upload generated videos and delete temporary reference/source files
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
