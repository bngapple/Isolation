from __future__ import annotations

import json


def classify(headline: str, api_key: str) -> dict:
    default = {"impact": "none", "direction": "uncertain"}
    if not api_key:
        return default

    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=api_key, timeout=5)
        prompt = (
            f'Headline: "{headline}"\n'
            'Will this likely cause a large directional move in NQ futures in the next 15 minutes?\n'
            'Output ONLY JSON: {"impact": "none"|"low"|"medium"|"high", "direction": "bullish"|"bearish"|"uncertain"}'
        )
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=128,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(text)
    except Exception:  # pragma: no cover
        return default
