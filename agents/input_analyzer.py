"""
agents/input_analyzer.py
------------------------
Input Analyzer agent.

Responsibilities:
  - Understand the user query
  - Identify main topic, objective, report type, depth
  - Extract requirements, audience, constraints, keywords
  - Return structured AnalyzedRequest (no final report)
  
Business Summary: Understands what the user really wants. 
A user saying "write about AI" is vague; 
this agent clarifies the depth, type, and key constraints before we do any heavy lifting.

Logic: Takes raw text. 
Sends it to the LLM with strict instructions to output JSON 
containing main_topic, report_type (technical, business, etc.), depth, and key_requirements. 
Validates the JSON against the AnalyzedRequest model.
  
"""
from __future__ import annotations

from typing import Any, Optional

from agents import create_chat_agent
from models import AnalyzedRequest, UserRequest
from prompts import INPUT_ANALYZER_SYSTEM, build_input_analyzer_prompt
from utils import (
    get_logger,
    log_error,
    log_event,
    message_content_to_str,
    parse_json_to_model,
)

logger = get_logger(__name__)
AGENT_NAME = "input_analyzer"


def get_input_analyzer_agent() -> Any:
    return create_chat_agent(name=AGENT_NAME, instructions=INPUT_ANALYZER_SYSTEM)


async def run_input_analyzer(
    request: UserRequest | str,
    agent: Optional[Any] = None,
) -> AnalyzedRequest:
    """
    Analyze a user query and return AnalyzedRequest.

    Args:
        request: UserRequest model or raw query string.
        agent: Optional existing ChatAgent instance (for reuse/tests).

    Returns:
        AnalyzedRequest

    Raises:
        ValueError: empty query or unparseable model output.
        RuntimeError: agent/LLM execution failure.
    """
    if isinstance(request, UserRequest):
        query = request.query
    else:
        query = (request or "").strip()
        if not query:
            raise ValueError("Query must not be empty.")

    log_event("input_analysis_started", query_length=len(query))

    chat_agent = agent or get_input_analyzer_agent()
    user_prompt = build_input_analyzer_prompt(query)

    try:
        result = await chat_agent.run(user_prompt)
        raw_text = message_content_to_str(getattr(result, "text", None) or result)
    except Exception as exc:  # noqa: BLE001
        log_error("input_analysis_llm_failed", exc)
        raise RuntimeError(f"Input Analyzer LLM call failed: {exc}") from exc

    try:
        analyzed = parse_json_to_model(raw_text, AnalyzedRequest)
    except ValueError as exc:
        log_error("input_analysis_parse_failed", exc, raw_preview=raw_text[:500])
        raise ValueError(f"Input Analyzer returned invalid structured output: {exc}") from exc

    # Always preserve the true original query from the caller
    analyzed.original_query = query

    log_event(
        "input_analysis_completed",
        topic=analyzed.main_topic,
        report_type=analyzed.report_type.value,
        depth=analyzed.depth.value,
        requirements=len(analyzed.key_requirements),
    )
    return analyzed


def run_input_analyzer_sync(
    request: UserRequest | str,
    agent: Optional[Any] = None,
) -> AnalyzedRequest:
    """
    Synchronous wrapper for scripts/tests.
    Prefer run_input_analyzer() inside the async FastAPI / MAF workflow.
    """
    import asyncio

    return asyncio.run(run_input_analyzer(request, agent=agent))