"""
agents/__init__.py
------------------
LLM client + agent factory.
Uses google-genai (new official SDK) with built-in 429 rate limit retry.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from config import LLMProvider, settings
from utils import get_logger, message_content_to_str

logger = get_logger(__name__)


@dataclass
class AgentResponse:
    """Simple response object with .text"""
    text: str


class GeminiAgent:
    """
    Async agent: await agent.run(prompt)
    Uses google-genai (official new SDK) with 429 retry.
    """

    def __init__(self, name: str, instructions: str) -> None:
        self.name = name
        self.instructions = instructions
        self._model_name = settings.google_model
        self._api_key = settings.google_api_key
        self._temperature = settings.temperature
        self._max_tokens = settings.max_tokens

    def _generate_text(self, prompt: str) -> str:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self._api_key)

        full_prompt = (
            f"{self.instructions.strip()}\n\n"
            f"---\n\n"
            f"{prompt.strip()}"
        )

        config = types.GenerateContentConfig(
            temperature=self._temperature,
            max_output_tokens=self._max_tokens,
        )

        last_error = None
        for attempt in range(1, 7):  # up to 6 tries
            try:
                response = client.models.generate_content(
                    model=self._model_name,
                    contents=full_prompt,
                    config=config,
                )

                text = getattr(response, "text", None)
                if text:
                    return text

                # Fallback parse
                try:
                    candidates = getattr(response, "candidates", None) or []
                    if candidates:
                        content = getattr(candidates[0], "content", None)
                        parts = getattr(content, "parts", None) or []
                        chunks = [getattr(p, "text", "") for p in parts if hasattr(p, "text")]
                        if chunks:
                            return "\n".join(chunks)
                except Exception:
                    pass

                return message_content_to_str(response)

            except Exception as exc:
                last_error = exc
                msg = str(exc)

                # Retry on rate limit (429)
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                    wait_s = 15 * attempt
                    logger.warning(
                        "Gemini rate limit (429). Waiting %ds (attempt %d/6)...",
                        wait_s, attempt,
                    )
                    time.sleep(wait_s)
                    continue

                # Retry on server overload (503) and gateway errors
                if any(c in msg for c in ("503", "UNAVAILABLE", "502", "504", "500", "INTERNAL")):
                    wait_s = 4 * attempt          # 4, 8, 12, 16, 20s
                    logger.warning(
                        "Gemini unavailable (5xx). Waiting %ds (attempt %d/6)...",
                        wait_s, attempt,
                    )
                    time.sleep(wait_s)
                    continue

                # Retry on transient network errors
                if any(t in msg.lower() for t in ("timeout", "connection reset", "connection aborted", "deadline")):
                    wait_s = 4 * attempt
                    logger.warning(
                        "Network error. Waiting %ds (attempt %d/6)...",
                        wait_s, attempt,
                    )
                    time.sleep(wait_s)
                    continue

                # Permanent errors — fail fast (404, 401, 400)
                raise

        raise RuntimeError(
            f"Gemini unavailable after 6 retries: {last_error}"
        ) from last_error

    async def run(self, prompt: str) -> AgentResponse:
        import asyncio

        text = await asyncio.to_thread(self._generate_text, prompt)
        return AgentResponse(text=text or "")


class AzureOpenAIAgent:
    """Optional Azure path when LLM_PROVIDER=azure_openai."""

    def __init__(self, name: str, instructions: str) -> None:
        self.name = name
        self.instructions = instructions

    def _generate_text(self, prompt: str) -> str:
        from openai import AzureOpenAI

        client = AzureOpenAI(
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            azure_endpoint=settings.azure_openai_endpoint,
        )
        completion = client.chat.completions.create(
            model=settings.azure_openai_deployment,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            messages=[
                {"role": "system", "content": self.instructions},
                {"role": "user", "content": prompt},
            ],
        )
        return completion.choices[0].message.content or ""

    async def run(self, prompt: str) -> AgentResponse:
        import asyncio

        text = await asyncio.to_thread(self._generate_text, prompt)
        return AgentResponse(text=text or "")


def create_chat_agent(
    name: str,
    instructions: str,
    tools: Optional[list] = None,
) -> Any:
    """Factory used by all agent modules."""
    provider = settings.llm_provider

    if provider == LLMProvider.GOOGLE:
        logger.info(
            "Created GeminiAgent name=%s model=%s",
            name,
            settings.google_model,
        )
        return GeminiAgent(name=name, instructions=instructions)

    if provider == LLMProvider.AZURE_OPENAI:
        logger.info(
            "Created AzureOpenAIAgent name=%s deployment=%s",
            name,
            settings.azure_openai_deployment,
        )
        return AzureOpenAIAgent(name=name, instructions=instructions)

    raise ValueError(
        f"Unsupported provider '{provider}'. Use google or azure_openai."
    )


# Lazy getters
def get_input_analyzer_agent():
    from agents.input_analyzer import get_input_analyzer_agent as _f
    return _f()


def get_planner_agent():
    from agents.planner import get_planner_agent as _f
    return _f()


def get_researcher_agent():
    from agents.researcher import get_researcher_agent as _f
    return _f()


def get_analyst_agent():
    from agents.analyst import get_analyst_agent as _f
    return _f()


def get_validator_agent():
    from agents.validator import get_validator_agent as _f
    return _f()


def get_report_writer_agent():
    from agents.report_writer import get_report_writer_agent as _f
    return _f()


__all__ = [
    "create_chat_agent",
    "GeminiAgent",
    "AzureOpenAIAgent",
    "AgentResponse",
    "get_input_analyzer_agent",
    "get_planner_agent",
    "get_researcher_agent",
    "get_analyst_agent",
    "get_validator_agent",
    "get_report_writer_agent",
]