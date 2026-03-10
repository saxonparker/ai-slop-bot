"""Google Gemini text generation backend."""

import os

from google import genai


class GeminiProvider:
    """Text generation using Google Gemini."""

    def generate(self, system: str, prompt: str) -> str:
        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        response = client.models.generate_content(
            model=os.environ.get("TEXT_MODEL", "gemini-2.5-flash"),
            contents=prompt,
            config={"system_instruction": system},
        )
        return response.text
