from __future__ import annotations

import json

from governor.risk_profile import RiskProfile
from utils.logger import get_logger


class ClaudeClient:
    def __init__(self, api_key: str, timeout: int = 10):
        self.api_key = api_key
        self.timeout = timeout
        self.last_profile = RiskProfile()
        self.logger = get_logger("claude_client")
        self._client = None

        if api_key:
            try:
                from anthropic import Anthropic

                self._client = Anthropic(api_key=api_key, timeout=timeout)
            except Exception as exc:  # pragma: no cover
                self.logger.error("anthropic init failed", extra={"data": {"error": str(exc)}})

    def decide(self, prompt: str) -> RiskProfile:
        if self._client is None:
            return self.last_profile

        try:
            response = self._client.messages.create(
                model="claude-opus-4-6",
                max_tokens=512,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            profile = RiskProfile.from_dict(json.loads(text))
            self.last_profile = profile
            return profile
        except Exception as exc:  # pragma: no cover
            self.logger.error("claude decision failed", extra={"data": {"error": str(exc)}})
            return self.last_profile or RiskProfile()
