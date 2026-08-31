"""Google Gemini (Nano Banana) image generation backend."""

import os

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from usage import (
    COST_PER_IMAGE,
    GenerationResult,
    ProviderGenerationError,
    classify_gemini_error,
)


# finish_reason / block_reason values that mean "declined for safety/policy
# reasons" rather than some other failure (MAX_TOKENS, OTHER, RECITATION, ...).
SAFETY_FINISH_REASONS = {
    "SAFETY", "PROHIBITED_CONTENT", "SPII", "BLOCKLIST",
    "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT", "IMAGE_RECITATION",
}
SAFETY_BLOCK_REASONS = {
    "SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "IMAGE_SAFETY",
    "MODEL_ARMOR", "JAILBREAK",
}


class GeminiProvider:
    """Image generation using Google Gemini Nano Banana."""

    def generate(self, prompt: str, references: list | None = None) -> GenerationResult:
        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        model = os.environ.get("IMAGE_MODEL", "gemini-3.1-flash-image")
        contents = [prompt]
        for reference in references or []:
            contents.append(
                types.Part.from_bytes(
                    data=reference.data,
                    mime_type=reference.mime_type,
                )
            )
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                ),
            )
        except genai_errors.APIError as exc:
            error_type, user_message = classify_gemini_error(exc)
            raise ProviderGenerationError(
                str(exc),
                backend="gemini",
                model=model,
                error_type=error_type,
                user_message=user_message,
                cost_estimate=COST_PER_IMAGE["gemini"],
            ) from exc

        if not response.candidates:
            block_reason = getattr(response.prompt_feedback, "block_reason", None)
            reason_name = getattr(block_reason, "name", None)
            print(
                f"GEMINI IMAGE: No candidates returned. block_reason={reason_name}. "
                f"Full response: {response}"
            )
            if reason_name in SAFETY_BLOCK_REASONS:
                raise ProviderGenerationError(
                    f"Prompt blocked before generation: block_reason={reason_name}",
                    backend="gemini",
                    model=model,
                    error_type="moderation",
                    user_message=(
                        "Gemini declined to generate this — the prompt was flagged by "
                        "its safety filters before generation even started. Try rephrasing."
                    ),
                    cost_estimate=COST_PER_IMAGE["gemini"],
                )
            raise ProviderGenerationError(
                "Gemini returned no candidates — prompt may have been blocked "
                f"(block_reason={reason_name})",
                backend="gemini",
                model=model,
                error_type="provider_error",
                user_message="Gemini returned no result for this prompt. Try again.",
                cost_estimate=COST_PER_IMAGE["gemini"],
            )

        candidate = response.candidates[0]
        finish_name = (
            getattr(candidate.finish_reason, "name", None) if candidate.finish_reason else None
        )
        if finish_name and finish_name != "STOP":
            print(f"GEMINI IMAGE: finish_reason={candidate.finish_reason}")

        text_parts = []
        for part in candidate.content.parts:
            if part.inline_data is not None:
                return GenerationResult(
                    content=part.inline_data.data,
                    backend="gemini",
                    model=model,
                    input_tokens=0,
                    output_tokens=0,
                    cost_estimate=COST_PER_IMAGE["gemini"],
                )
            if part.text is not None:
                text_parts.append(part.text)

        # No image — log whatever text Gemini returned instead
        text_response = " ".join(text_parts) if text_parts else "(no text either)"
        print(f"GEMINI IMAGE: No image in response. Text returned: {text_response}")
        if finish_name in SAFETY_FINISH_REASONS:
            raise ProviderGenerationError(
                f"No image generated (finish_reason={finish_name}). Gemini said: {text_response}",
                backend="gemini",
                model=model,
                error_type="moderation",
                user_message=(
                    "Gemini declined to generate this image "
                    f"({finish_name.replace('_', ' ').lower()})."
                    + (f" It said: {text_response}" if text_parts else "")
                    + " Try rephrasing."
                ),
                cost_estimate=COST_PER_IMAGE["gemini"],
            )
        # finish_reason == STOP (or unset) but no image — Nano Banana explained
        # itself in freeform text instead of drawing. That text is usually
        # already the best human-readable answer, so surface it directly.
        raise ProviderGenerationError(
            f"No image generated. Gemini said: {text_response}",
            backend="gemini",
            model=model,
            error_type="provider_error",
            user_message=f"Gemini didn't generate an image. It said: {text_response}",
            cost_estimate=COST_PER_IMAGE["gemini"],
        )
