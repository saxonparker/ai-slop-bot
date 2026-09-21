---
name: update-ai-slop-models
description: Check current provider releases and automatically update AI Slop Bot's supported models, API adapters, pricing, configuration, and tests. Use for model refreshes in the ai_slop repository, especially new OpenAI image models.
---

# Update AI Slop models

Check the providers used by this repository and implement verified model upgrades
in the working tree. An invocation requests the complete update, including
compatibility fixes and tests; do not stop after listing newer model names.
Honor a narrower provider or modality scope when the user gives one.

Locate the checkout from the current workspace. It contains `ai_slop_bot/`,
`ai_slop_dispatch/`, and `terraform/`. The usual checkout on Saxon's machine is
`/home/saxon/scratch/ai_slop`; do not modify another project that happens to use AI.

## Verify releases and choose replacements

Read the current model configuration and adapters, then search and open current
official documentation for exact API model IDs, release status, endpoint support,
request/response changes, prices, and deprecation dates. If an available OpenAI
documentation skill prescribes its own source order, follow that order for OpenAI.

Start with these catalogs and follow links to model and migration documentation:

- OpenAI: https://developers.openai.com/api/docs/models and
  https://developers.openai.com/api/docs/guides/image-generation
- Google: https://ai.google.dev/gemini-api/docs/models and
  https://ai.google.dev/gemini-api/docs/pricing
- Anthropic: https://platform.claude.com/docs/en/models/overview
- xAI: https://docs.x.ai/developers/models and
  https://docs.x.ai/developers/release-notes

Use official sources for implementation decisions. A model-list API can establish
account visibility when existing credentials are available, but does not establish
capabilities, pricing, or that an announcement is a generally available API model.
Do not infer capability by sorting IDs, release dates, or version numbers alone.

By default, upgrade to a newer supported model serving the same cost, latency,
quality, and modality role. Keep provider defaults and explicit environment
overrides intact. A more expensive flagship or a new modality is not automatically
a replacement for an inexpensive existing route. Retired aliases may redirect to
a differently priced model; account for the documented redirect. If a candidate
has unresolved compatibility or price differences, complete the verified upgrades
and report that candidate and the specific blocker. Do not invent model names or
rates when docs are unavailable. If the user asks for a check only, report findings
without editing.

## Apply a complete upgrade

Repository map:

- `ai_slop_bot/model_config.py`: shared defaults and environment precedence.
  Generation adapters and failed-request labels both use `get_model`.
- `ai_slop_bot/backends/`: provider API calls, response decoding, token counts,
  and provider-specific errors. Preserve chat history and reference media support.
- `ai_slop_bot/usage.py`: model-specific text rates, image token estimates,
  video rates, actual billed cost handling, and video mode classification.
- `ai_slop_bot/ai_slop_bot.py`: failure-cost estimates, media validation, and
  orchestration. Preserve the -$5 prompt override and -$10 generation cutoff.
- `terraform/variables.tf` and `terraform/lambdas.tf`: deployed overrides can
  mask Python defaults. Update affected default values and environment wiring.
- `README.md` and `ai_slop_dispatch/ai_slop_dispatch.py`: model table, configuration,
  and user-facing help. Record the review date and official source links.
- `ai_slop_bot/Makefile`: include new runtime modules in the Lambda package.

OpenAI images require checking both generation and edits. GPT Image uses base64
responses and GPT-specific quality settings; old DALL-E URL handling and `hd`
settings cannot simply be carried over. Verify reference-file shape and supported
sizes. Derive cost estimates from returned token breakdowns and published rates;
never label calculated estimates as provider-reported actual charges.

For text upgrades, inspect reasoning defaults and response content blocks. Keep
non-reasoning routes non-reasoning when supported. Count billable thinking tokens,
verify output limits, and handle published pricing transitions without rewriting
historical ledger or usage rows. Keep pricing estimates explicit when cached-token
discounts or exact billed-dollar data are unavailable.

Add focused mocked tests for changed request schemas, response decoding, reference
edits, model overrides, cost calculations, and failure labels. Retain old model
names in tests that intentionally represent historical records or explicit pins.
If a candidate requires a substantially different integration, report it rather
than breaking an existing adapter with a model-ID-only replacement.

## Validate and finish

From `ai_slop_bot`, run `.venv/bin/python -m pytest tests/ -q` if that environment
exists, otherwise use the project's available Python environment. Run relevant
lint and `git diff --check`; distinguish existing lint findings from new ones.
Check Terraform formatting when Terraform files changed and the CLI is available.
Use mocked API calls for regression tests. Report separately whether live model
access was verified; do not represent mocked tests as provider validation.

Report old/new IDs, price or behavior changes, deferred candidates, checks run,
and source links. Automatic updates mean applying changes when this skill runs;
the skill itself does not create a recurring schedule. Commit, push, deploy, or
schedule only when the user's current instructions authorize those actions.
