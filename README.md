# ai-slop

Unified Slack AI command (`/slop-bot`) with pluggable provider backends.

## Usage

The user-facing Slack slash command is `/slop-bot`. The Slack app still posts
slash-command payloads to the `/ai-slop` HTTP route during deployment.

- `/slop-bot <prompt>` — text response (default: Gemini)
- `/slop-bot -i <prompt>` — image generation (default: Grok)
- `/slop-bot -v [seconds] <prompt>` — video generation (default: Grok)
- `/slop-bot -e <prompt>` — emoji-only text response
- `/slop-bot -bufo <prompt>` or `/slop-bot --bufo <prompt>` — sentiment-analyzed bufo-emoji-only rewriting sourced from bufopedia.com
- `/slop-bot -p <prompt>` — potato mode
- `/slop-bot -b gemini <prompt>` — text with a specific backend
- `/slop-bot -i -b openai <prompt>` — image with a specific backend
- `/slop-bot -v -b grok <prompt>` — video with a specific backend
- `/slop-bot -v -b gemini <prompt>` — video with Veo (native audio/dialogue)
- `/slop-bot -v -r 720 <prompt>` — video at a chosen resolution (Grok only)

### Flags

Flags can appear in any order unless a flag consumes the next value.

- `-i` — image generation.
- `-v [seconds]` — video generation. Grok defaults to 10 seconds and supports up to 15 seconds; Grok reference-to-video supports up to 10 seconds. Veo (`-b gemini`) supports 4, 6, or 8 seconds and snaps other requested durations to the nearest supported value.
- `-e` — emoji-only response.
- `-bufo`, `--bufo` — sentiment-analyzed bufo-emoji-only rewriting sourced from bufopedia.com.
- `-p` — potato mode.
- `-b <backend>` — override the backend for the selected mode.
- `-u`, `--usage` — show your usage stats and credit balance.
- `-g`, `--gallery` — show the AI Slop Gallery link.
- `-pay <amount>`, `--pay <amount>` — keep the existing immediate credit and Venmo payment link until live PayPal is explicitly enabled.
- `-pay-test <amount>`, `--pay-test <amount>` — test the new PayPal checkout in Sandbox; no real money or spendable credits.
- `--upload` — open the Slack upload modal; combine with `-i` or `-v`.
- `--edit <image-url>` — edit an image from a URL; with `-i --edit` and no URL, open the upload modal for an uploaded image edit.
- `--ref <image-url>` — add an image reference. Repeat for multiple references.
- `--start <image-url>` — use an image URL as the start frame for a video.
- `--voice <voice-id>` — add a preset voice to a Grok video. Repeat for up to 3 voices.
- `-r <resolution>`, `--resolution <resolution>` — Grok video output resolution. Accepts `480`, `720`, `1080`, or the same values with a trailing `p`. Billed per second at a rate that scales with resolution, so this is also a spend control.
- `--edit-video <video-url>` — edit an existing video (Grok only).
- `--extend-video <video-url>` — extend a video from its last frame (Grok only).
- `--report` (`-report` is also accepted) — admin-only balance report; the caller must be listed in `ADMIN_USERS`.
- `--credit <user> <amount>` (`-credit` is also accepted) — admin-only credit adjustment; the caller must be listed in `ADMIN_USERS`, and the amount can be negative.

### Bracket syntax

- `[hidden directive]` — included in the AI prompt but removed from the visible Slack prompt.
  Example: `/slop-bot tell me a joke [make it about dogs]`.
- `]shown text[` — shown in Slack but removed from the AI prompt.
  Example: `/slop-bot what's the capital of France? ]asking for a friend[`.

### Conversations

Every plain text reply carries a *Continue* button. Clicking it opens a short
form; the follow-up is posted in the channel as a new reply with its own
button, so an exchange can run for many turns without threads or mentions.

- Anyone in the channel can continue a conversation, and each person pays for
  their own turns under the usual balance rules.
- The first prompt's `-b`, `-p`, and `-e` choices apply to every later turn;
  `[hidden]` and `]shown[` bracket syntax still works in follow-ups.
- `-bufo`, image, and video replies are single-shot and have no button. A
  payment-reminder reply is never continuable.
- Conversations stop after 20 turns or roughly 60,000 stored characters and
  expire 30 days after their last turn. The button stays valid for 30 minutes
  after it is clicked, so submit the form promptly.
- Requires `CONVERSATIONS_TABLE_NAME`; without it, replies have no button.

### Reference images and videos

There are two ways to provide reference media for generated content:

- Use URL flags directly in the slash command:
  - `--edit <image-url>` for image edits.
  - `--ref <image-url>` for image or video style/content references.
  - `--start <image-url>` for a video start frame.
  - `--voice <voice-id>` for a Grok video narration voice.
  - `--edit-video <video-url>` for Grok video edits from an existing video.
  - `--extend-video <video-url>` for Grok video extensions from the source video's last frame.
- Use the Slack upload modal:
  - `/slop-bot -i --upload` opens an image prompt form with 1-3 uploaded references.
  - `/slop-bot -i --edit` opens the same form for editing an uploaded image.
  - `/slop-bot -i --edit make this watercolor` opens the form with the prompt pre-filled.
  - `/slop-bot -v --upload` opens a video prompt form where image uploads can be a single start frame or loose references, source video uploads can be used for edit/extend, and up to 3 built-in voices can be selected.

Uploaded modal files are temporary. Slack stores them briefly, then the bot
downloads and deletes them after it has normalized images or staged source
videos through the existing S3/CloudFront upload path for provider calls.
Generated outputs still use the same S3/CloudFront upload path.

Backend support differs slightly:

- Grok image supports up to 5 reference images for edits; Grok video supports reference images.
- Grok video supports `--edit-video` and `--extend-video` source-video operations.
- Grok video supports up to 3 preset voices via `--voice`; Gemini video does not.
- Gemini image supports reference images.
- Gemini video supports one start image, but not loose references or video edit/extend operations.
- OpenAI image uses the edit model when references are supplied.
- Video edit/extend flags are Grok-only; use `-b grok` if `VIDEO_BACKEND` is not `grok`.

### Video voices

Grok video accepts up to 3 preset voices. Add them with `--voice <voice-id>`, or
pick them from the `-v --upload` form, which lists xAI's 28 built-in voices with
their gender tag (`Eve (F)`, `Leo (M)`, ...). The flag accepts any voice id, so
custom voices and any roster additions work without a dispatch redeploy.

xAI binds voices to speakers by `<AUDIO_0>`, `<AUDIO_1>`, and `<AUDIO_2>` tags in
the prompt, so write them yourself to control who says what:

```text
/slop-bot -v --voice eve --voice leo the host with <AUDIO_0> interviews the guest with <AUDIO_1>
```

A prompt with no `<AUDIO_` tag gets one sentence appended naming the tags, so a
bare `--voice eve` still produces narration.

### Video resolution

Grok video renders at 1080p by default. Pick a different size per request with
`-r` (or `--resolution`), which accepts `480`, `720`, and `1080`, with or without
a trailing `p`:

```text
/slop-bot -v -r 720 a corgi surfing
```

`-r` overrides the deployment-wide `VIDEO_RESOLUTION` default. xAI caps
reference-guided generation at 720p, so requests using reference images or
voices are clamped to 720p automatically even when `-r 1080` is passed.

`-r` is rejected rather than ignored where xAI cannot honor it: on `-b gemini`,
on non-video requests, and with `--edit-video`/`--extend-video` (those match the
source clip's resolution, capped at 720p).

Resolution drives cost. xAI bills `grok-imagine-video-1.5` per second at a rate
that scales steeply with output size, so `-r` is also a spend control:

| Resolution | Rate | 10-second clip |
|---|---|---|
| `480p` | $0.08 / sec | $0.80 |
| `720p` | $0.14 / sec | $1.40 |
| `1080p` (default) | $0.25 / sec | $2.50 |

Estimates are computed against the resolution xAI actually renders, not the one
requested — a `-r 1080` request clamped to 720p by reference images is estimated
at the 720p rate. Video edit/extend inherits the source clip's resolution capped
at 720p, and the request never states it, so those are estimated at that cap.
Recorded actual cost still comes from the response's `cost_in_usd_ticks` when
present; these rates are the pre-flight and failed-attempt estimates.

## Budget & Credits

Balances are calculated as:

```text
balance = ledger credits - usage costs
```

Credits live in the DynamoDB ledger table named by `LEDGER_TABLE_NAME`
(default: `ai-slop-ledger`). Usage costs come from `USAGE_TABLE_NAME` (default:
`ai-slop-usage`) and use actual billed cost when available, otherwise the
stored estimate.

`/slop-bot -pay <amount>` preserves the existing flow by default: it immediately
records the credit and returns a Venmo payment link. Deploying the new checkout
code or setting up Sandbox does not switch real payments to PayPal.

Use `/slop-bot -pay-test 10` to exercise the new checkout through the separate
sandbox Lambda. Test payments only credit `ai-slop-ledger-sandbox`; they never
change the user's spendable balance. If Sandbox is unavailable, the test command
reports that and regular `-pay` continues to work.

After testing, explicitly setting the GitHub variable `PAYPAL_LIVE_ENABLED=true`
and deploying switches `-pay` to verified PayPal/Venmo checkout for $1–$500 USD.
Credits then require a completed capture matching the purchase, amount, and
currency, and duplicate captures/webhooks cannot credit it twice. A checkout
failure after that switch never falls back to immediate credit. See
[PayPal setup and sandbox testing](docs/paypal.md).

`/slop-bot -u` shows the caller's usage summary plus their current balance. The
balance display includes the latest ledger entry amount/date when one exists.

Before each generation, the bot checks the requesting user's balance:

- Above -$5, prompts work normally.
- At or below -$5 (but above -$10), the prompt is replaced with a "pay Saxon
  money" reminder, poster, or commercial for text, images, or videos. This
  overrides emoji, bufo, and potato modes too.
- At or below -$10, generation is blocked without calling a provider. The reply
  explains how to add credits with `/slop-bot -pay <amount>` and pay through the
  returned payment link (Venmo by default, verified checkout after the live switch).

These limits also apply to uploaded media requests. Usage, payment, gallery,
and authorized admin commands remain available. Adding credits restores normal prompts once the balance is
above -$5. If the balance cannot be retrieved, generation waits for a retry.
The check uses recorded costs before the request; an in-flight request can
still push the balance past a threshold.

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

Every photo and video has a permalink. Open it in the viewer and use
**Copy link** to get
`https://d2jagmvo7k5q5j.cloudfront.net/index.html?item=<file name>`, where
the file name is the S3 key without `dalle/`. Copied links always use the
CloudFront host, even from the direct S3 URL, because that host is the Slack
unfurl domain. The address bar follows the open item too. A link copied from
there in Hall of Fame keeps `#hall-of-fame` and opens in All if the item is no
longer a pick. Links to deleted items open the gallery with a notice.

Pasting a gallery link into Slack previews it:
- Photos show the image.
- Videos show an inline player with a frame from the video as its thumbnail.
  Both `player.html` and Slack's preview use the same JPEG. If extraction failed
  or an older video has not been backfilled, they use `video-poster.png`.
  If Slack rejects the embed, the bot posts a title card with the same thumbnail.
- Direct CloudFront media URLs (`…/dalle/…`) preview the same way.
- The Hall of Fame message shortcut also accepts messages that share a
  permalink.

The bot handles Slack's `link_shared` event and replies with `chat.unfurl`
(see [Unfurling links in messages](https://docs.slack.dev/messaging/unfurling-links-in-messages/)).
This needs the Slack app settings in [First-time setup](#first-time-setup).

The bot Lambda loads FFmpeg from a layer containing the pinned `imageio-ffmpeg`
wheel. The normal bot `make` builds both `ai_slop_bot.zip` and `ffmpeg_layer.zip`,
keeping each below Lambda's 50 MiB direct-upload limit. New
gallery video uploads extract a frame at 1 second (the first frame for clips
shorter than that), fit it within 640×640, and save a JPEG at
`thumbnails/<SHA-256 of the full decoded dalle/... key>.jpg`. The separate prefix
keeps posters out of the gallery listing. Extraction has a 10-second deadline;
failures are logged and the original video still uploads/posts. Source videos
temporarily staged for editing do not get thumbnails. The deployment build
checks the Lambda package size and runs real extraction in the Python 3.12
Lambda base image. No new Slack settings or link parameters are needed.

Backfill existing videos with the same decoder and AWS credentials that can
list the bucket, read videos, and read/write `thumbnails/`:

```bash
cd ai_slop_bot
pipenv install
pipenv run python backfill_video_thumbnails.py              # dry run
pipenv run python backfill_video_thumbnails.py --apply --limit 10
pipenv run python backfill_video_thumbnails.py --apply       # remaining videos
```

The script skips existing posters and exits nonzero if any item fails. Use
`--key 'dalle/example_ABC.mp4'` to target a single video. Existing gallery links
then use the thumbnail when shared again; previously posted Slack previews
are not rewritten. CloudFront may briefly cache a missing poster response.

The **Hall of Fame** tab collects community favorites. Anyone with the gallery
link can use the trophy control on a photo or video to add it, or remove it
directly from Hall of Fame (including in the modal viewer). Removal only
changes the selection; the original remains in All/Photos/Videos. Search and
user/channel filters also work inside Hall of Fame. Link directly to it with
`https://d2jagmvo7k5q5j.cloudfront.net/index.html#hall-of-fame`.

In Slack, use a generated message's **… → Add to Hall of Fame** shortcut.
Normal generation posts have no extra buttons or messages. The shortcut sends
a private confirmation with an undo button. Existing image posts work; videos
uploaded after this feature is deployed are linked by their Slack file ID.
Older videos can be selected on the website. Messages containing multiple
gallery items also direct you to the website to choose the right one.

Configure the Slack shortcut once under **Interactivity & Shortcuts → Create
New Shortcut → On messages**:

- Name: `Add to Hall of Fame`
- Description: `Save a generated photo or video to the gallery's Hall of Fame`
- Callback ID: `hall_of_fame_add`
- Use the existing interactivity Request URL (`terraform output -raw slack_interactions_url`).
- Keep the existing `commands` scope (already required by `/slop-bot`).

See [Slack's message shortcut setup](https://docs.slack.dev/interactivity/implementing-shortcuts/).

Terraform creates `ai-slop-hall-of-fame` (one row per selected media key) and
`ai-slop-gallery-media` (Slack video file ID → gallery key). The dispatch Lambda
serves public `GET`/`PUT /gallery/hall-of-fame`; Slack requests retain their normal
signature verification and asynchronous processing. Curation intentionally
requires no website login. Writes validate media keys and verify that newly
featured files exist in S3. No browser S3 write permissions are needed.
The API permits both the CloudFront gallery URL and the existing direct S3
URL, `https://dallepics.s3.us-east-2.amazonaws.com/index.html`.

The deploy workflow publishes `gallery/config.json` from Terraform's
`gallery_config` output, then publishes the gallery HTML, `player.html`, and
`video-poster.png` to the bucket root. Manual deployments must publish all of
them. The configuration contains only the public API URL.
The website refreshes selections on load and when you return to its window;
failed reads/saves show an error without silently changing membership.

Run browser coverage with `npm ci`, `npx playwright install chromium`, and
`npm run test:gallery`. Backend/Slack coverage runs with the usual
`cd ai_slop_bot && pytest tests/`.

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
| Text  | anthropic  | `claude-sonnet-5`                 |         |
| Text  | gemini     | `gemini-3.8-flash`                | Yes     |
| Text  | openai     | `gpt-5.6-sol`                    |         |
| Text  | grok       | `grok-4.3` (reasoning off)       |         |
| Image | gemini     | `gemini-3.1-flash-image`          |         |
| Image | openai     | `gpt-image-2.5-flare`             |         |
| Image | grok       | `grok-imagine-image-2.0`          | Yes     |
| Video | grok       | `grok-imagine-video-1.5`          | Yes     |
| Video | gemini     | `veo-3.1-fast-generate-preview`   |         |

OpenAI reference edits default to `gpt-image-2.5-sunburst`. Both GPT Image 2.5
models support generation and editing; select either with `OPENAI_IMAGE_MODEL`
or `OPENAI_IMAGE_EDIT_MODEL`. Images use medium quality at 1024x1024, and costs
are estimated from the returned text/image token breakdown. Without token usage,
the existing $0.08 image estimate is used. Cached-token discounts are not included
in these estimates.

### Model maintenance

Use `$update-ai-slop-models` in Codex to check releases and apply supported
upgrades, API compatibility changes, pricing updates, and tests. The versioned
skill lives in [skills/update-ai-slop-models](skills/update-ai-slop-models/SKILL.md);
install that folder under `~/.codex/skills/` for personal discovery. It runs when
invoked and does not create a recurring schedule or deploy changes.

Model defaults are shared in `ai_slop_bot/model_config.py`. Last reviewed
2026-09-21 against [OpenAI models](https://developers.openai.com/api/docs/models),
[OpenAI images](https://developers.openai.com/api/docs/guides/image-generation),
[Gemini models](https://ai.google.dev/gemini-api/docs/models),
[Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing),
[Claude models](https://platform.claude.com/docs/en/models/overview), and
[xAI models](https://docs.x.ai/developers/models).

The Grok text route uses the documented replacement for its
[retired non-reasoning alias](https://docs.x.ai/developers/migration/may-15-retirement).
Grok 4.7 is available via `TEXT_MODEL` but is a higher-priced tier. Gemini Omni
video needs a separate integration; the existing Veo adapter remains in use.
OpenAI's Sora API is omitted because its
[scheduled shutdown is September 24, 2026](https://developers.openai.com/api/docs/deprecations#2026-03-24-sora-2-video-generation-models-and-videos-api).
Text estimates use model-specific standard rates. Gemini 3.8 Flash's published
January 2027 rate change is applied by date. OpenAI's current GPT-5.6 Sol pricing
is promotional through at least November 21, 2026; recheck rates on later reviews.

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
| `VIDEO_RESOLUTION` | `1080p` | Default Grok output resolution (`480p`, `720p`, `1080p`) when `-r` is not given; clamped to `720p` when references or voices are used |
| `ANTHROPIC_API_KEY` | — | Required if using anthropic backend |
| `GOOGLE_API_KEY` | — | Required if using gemini backends |
| `GROK_IMAGE_EDIT_TIMEOUT_SECONDS` | `180` | Timeout for Grok image edit requests |
| `OPENAI_API_KEY` | — | Required if using openai backends |
| `OPENAI_IMAGE_MODEL` | `gpt-image-2.5-flare` | OpenAI generation model; `IMAGE_MODEL` takes precedence when set |
| `OPENAI_IMAGE_EDIT_MODEL` | `gpt-image-2.5-sunburst` | OpenAI model used when reference images are supplied |
| `OPENAI_ORGANIZATION` | — | Required if using openai backends |
| `XAI_API_KEY` | — | Required if using grok backends |
| `SLACK_BOT_TOKEN` | — | Slack Web API token for posting responses, uploads, modals, reference downloads, and cleanup |
| `USAGE_TABLE_NAME` | `ai-slop-usage` | DynamoDB usage table for request records, usage summaries, balances, and audit CLI |
| `LEDGER_TABLE_NAME` | `ai-slop-ledger` | DynamoDB credit ledger table for payments and admin adjustments |
| `CONVERSATIONS_TABLE_NAME` | unset | DynamoDB table for Continue-button conversations; replies have no button when unset |
| `VENMO_USERNAME` | `Saxon-Parker` | Venmo username for the existing -pay flow; verified checkout uses the configured PayPal merchant account |
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
       /slop-bot <prompt> | -i | -v [sec] | -g/--gallery | --upload | --edit [img-url] | --ref/--start <img-url> | --voice <voice-id> | --edit-video/--extend-video <video-url> | -b <backend> | -e | -bufo/--bufo | -p | -u | -pay <amt>
       ```
   - **Interactivity & Shortcuts** → Enable Interactivity
     - Request URL: `<base_url>/slack/interactions`
   - **Event Subscriptions** → Enable Events
     - Request URL: `<base_url>/slack/events`
     - Subscribe to bot event: `link_shared`
     - **App Unfurl Domains**: `d2jagmvo7k5q5j.cloudfront.net`. Use this exact
       host; `cloudfront.net` would claim every CloudFront link in the
       workspace. The app then answers previews for every link on the host:
       permalinks and `dalle/` media get previews, and other paths get none.
   - **OAuth & Permissions** → Bot Token Scopes:
     - `chat:write` - write messages
     - `commands` - receive slash commands
     - `files:read` - read uploaded reference images and source videos
     - `files:write` - upload generated videos and delete temporary reference/source files
     - `links:read` / `links:write` — preview gallery links (`link_shared`, `chat.unfurl`)
     - `links.embed:write` — play gallery videos inline in those previews
   - Reinstall the app to your workspace after changing scopes; copy the
     new Bot User OAuth Token into the `slack_bot_token` Terraform variable.

### Manual deploy

```bash
cd ai_slop_bot && make
cd ai_slop_dispatch && make
cd terraform && terraform init && terraform apply
```
