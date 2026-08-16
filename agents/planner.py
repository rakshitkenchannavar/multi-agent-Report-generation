"""
agents/planner.py
-----------------
Planning agent.

Responsibilities:
  - Receive AnalyzedRequest
  - Create a dynamic report structure (sections / subsections)
  - Generate research questions
  - Define scope, assumptions, success criteria
  - Return structured ReportPlan
  
Business Summary: Creates the blueprint for the report. 
Decides the chapters and what questions need answering. 
It adapts the structure dynamically based on the topic (a tech report looks different from a business report).
Logic: Takes the AnalyzedRequest. 
Instructs the LLM to dynamically generate a table of contents (sections/subsections) 
based on the topic depth, and formulates specific research_questions for each section. 
Normalizes section IDs and sorts them so they flow logically.
"""

from __future__ import annotations

from typing import Any, Optional

from agents import create_chat_agent
from models import AnalyzedRequest, ReportPlan
from prompts import PLANNER_SYSTEM, build_planner_prompt
from utils import (
    get_logger,
    log_error,
    log_event,
    message_content_to_str,
    model_to_pretty_json,
    parse_json_to_model,
)

logger = get_logger(__name__)

AGENT_NAME = "planner"


def get_planner_agent() -> Any:
    """Create the Planning agent."""
    return create_chat_agent(
        name=AGENT_NAME,
        instructions=PLANNER_SYSTEM,
    )


def _normalize_plan(plan: ReportPlan, analyzed: AnalyzedRequest) -> ReportPlan:
    """Clean ids/order and attach analyzed request."""
    plan.analyzed_request = analyzed

    for idx, section in enumerate(plan.sections, start=1):
        if section.order <= 0:
            section.order = idx
        if not section.id:
            section.id = f"sec_{idx}"
        for sub_idx, sub in enumerate(section.subsections, start=1):
            if sub.order <= 0:
                sub.order = sub_idx
            if not sub.id:
                sub.id = f"{section.id}_sub_{sub_idx}"

    for idx, rq in enumerate(plan.research_questions, start=1):
        if not rq.id:
            rq.id = f"rq_{idx}"

    plan.sections = sorted(plan.sections, key=lambda s: s.order)

    if not plan.title or not plan.title.strip():
        plan.title = analyzed.main_topic

    return plan


async def run_planner(
    analyzed: AnalyzedRequest,
    agent: Optional[Any] = None,
) -> ReportPlan:
    """Build a ReportPlan from an AnalyzedRequest."""
    if analyzed is None:
        raise ValueError("AnalyzedRequest is required for planning.")

    log_event(
        "planning_started",
        topic=analyzed.main_topic,
        depth=analyzed.depth.value,
        report_type=analyzed.report_type.value,
    )

    chat_agent = agent or get_planner_agent()
    user_prompt = build_planner_prompt(model_to_pretty_json(analyzed))

    try:
        result = await chat_agent.run(user_prompt)
        raw_text = message_content_to_str(getattr(result, "text", None) or result)
    except Exception as exc:  # noqa: BLE001
        log_error("planning_llm_failed", exc)
        raise RuntimeError(f"Planner LLM call failed: {exc}") from exc

    try:
        plan = parse_json_to_model(raw_text, ReportPlan)
    except ValueError as exc:
        log_error("planning_parse_failed", exc, raw_preview=raw_text[:500])
        raise ValueError(f"Planner returned invalid structured output: {exc}") from exc

    plan = _normalize_plan(plan, analyzed)

    log_event(
        "planning_completed",
        title=plan.title,
        sections=len(plan.sections),
        research_questions=len(plan.research_questions),
        executive_summary_required=plan.executive_summary_required,
    )
    return plan


def run_planner_sync(
    analyzed: AnalyzedRequest,
    agent: Optional[Any] = None,
) -> ReportPlan:
    """Synchronous wrapper for scripts/tests."""
    import asyncio

    return asyncio.run(run_planner(analyzed, agent=agent))