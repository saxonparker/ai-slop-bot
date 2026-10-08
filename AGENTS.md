# Generation option parity

Every new or changed user-facing generation option must also be supported in the
unified Slack generation modal in the same change. This includes providers,
generation types, command flags, styles, media inputs, and provider-specific
settings. Do not consider a generation feature complete until its modal support
is implemented, unless the user explicitly requests a command-only feature.

When adding or changing an option:

- Add the appropriate controls to `ai_slop_dispatch/generation_modal.py`, showing
  them only for providers and operations that support them.
- Update command prefills, draft preservation, submission validation, and the
  structured generation payload. Keep prompts literal rather than reconstructing
  slash commands from form input.
- Update `ai_slop_bot/parsing.py` and the generation pipeline as needed so modal
  and command requests have equivalent behavior, limits, and billing rules.
- Cover the option with meaningful modal and generation tests, including relevant
  provider or operation changes and unsupported combinations.
- Update `README.md` and Slack command help to document both ways to use it.

Account and administrative commands, such as usage, payments, and credit
adjustments, are outside this generation-modal requirement.
