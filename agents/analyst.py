"""
agents/analyst.py
-----------------
Analyst agent.

Responsibilities:
  - Interpret research (do not merely copy it)
  - Extract key findings, trends, comparisons
  - Identify advantages, disadvantages, risks, opportunities when relevant
  - Produce section-level insights and executive overview
  - Incorporate validator improvement notes on retry
  - Return structured AnalysisResult
  
Business Summary: Makes sense of the raw research. 
It doesn't just copy-paste facts; 
it finds trends, pros/cons, and risks. 
This is where real business value is added.
Logic: Takes ResearchResult and the Plan. 
Instructs the LLM to interpret the data, extract key findings, 
identify SWOT (Strengths, Weaknesses, Opportunities, Threats), 
and map insights to specific report sections. 
If the Validator rejected it previously, 
it injects "improvement notes" so the LLM knows exactly what to fix.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from agents import create_chat_agent
from models import AnalysisResult, ReportPlan, ResearchResult, ValidationResult
from prompts import (
    ANALYST_SYSTEM,
    build_analyst_prompt,
    build_improvement_notes,
)
from utils import (
    get_logger,
    log_error,
    log_event,
    message_content_to_str,
    parse_json_to_model,
)

logger = get_logger(__name__)

AGENT_NAME = "analyst"


def get_analyst_agent() -> Any:
    """Create the Analyst agent (Gemini / configured provider)."""
    return create_chat_agent(
        name=AGENT_NAME,
        instructions=ANALYST_SYSTEM,
    )


def _plan_json_for_prompt(plan: ReportPlan) -> str:
    """Serialize plan for the prompt."""
    data = plan.model_dump(mode="json", exclude_none=True)
    return json.dumps(data, indent=2, default=str)


def _research_json_for_prompt(research: ResearchResult) -> str:
    data = research.model_dump(mode="json", exclude={"plan"}, exclude_none=True)
    return json.dumps(data, indent=2, default=str)


def _improvement_notes_from_validation(validation: Optional[ValidationResult]) -> str:
    if validation is None:
        return ""
    return build_improvement_notes(
        issues=validation.issues or [],
        missing_information=validation.missing_information or [],
        recommendations=validation.recommendations or [],
        summary=validation.summary,
    )


def _normalize_analysis(
    analysis: AnalysisResult,
    research: ResearchResult,
    plan: ReportPlan,
    attempt: int,
    improvement_notes: str,
) -> AnalysisResult:
    """Attach upstream context and attempt metadata."""
    analysis.research = research
    analysis.plan = plan
    analysis.attempt = max(1, attempt)
    if improvement_notes:
        analysis.improvement_notes = improvement_notes

    if analysis.factors is None:
        from models import SWOTOrFactors

        analysis.factors = SWOTOrFactors()

    if analysis.key_findings:
        analysis.key_findings = sorted(
            analysis.key_findings,
            key=lambda f: (f.importance, f.title or ""),
        )

    return analysis


async def run_analyst(
    research: ResearchResult,
    plan: Optional[ReportPlan] = None,
    validation_feedback: Optional[ValidationResult] = None,
    attempt: int = 1,
    agent: Optional[Any] = None,
) -> AnalysisResult:
    """
    Analyze research results and produce AnalysisResult.

    Args:
        research: Output from the researcher stage.
        plan: Report plan (falls back to research.plan if omitted).
        validation_feedback: Prior ValidationResult when retrying after FAIL.
        attempt: 1-based attempt number (increments on validation retry).
        agent: Optional agent instance.

    Returns:
        AnalysisResult
    """
    if research is None:
        raise ValueError("ResearchResult is required for analysis.")

    resolved_plan = plan or research.plan
    if resolved_plan is None:
        raise ValueError(
            "ReportPlan is required for analysis (pass plan or set research.plan)."
        )

    improvement_notes = _improvement_notes_from_validation(validation_feedback)

    log_event(
        "analysis_started",
        title=resolved_plan.title,
        attempt=attempt,
        research_items=len(research.items or []),
        has_improvement_notes=bool(improvement_notes),
    )

    user_prompt = build_analyst_prompt(
        plan_json=_plan_json_for_prompt(resolved_plan),
        research_json=_research_json_for_prompt(research),
        improvement_notes=improvement_notes or "",
        attempt=attempt,
    )

    chat_agent = agent or get_analyst_agent()

    try:
        result = await chat_agent.run(user_prompt)
        raw_text = message_content_to_str(getattr(result, "text", None) or result)
    except Exception as exc:  # noqa: BLE001
        log_error("analysis_llm_failed", exc, attempt=attempt)
        raise RuntimeError(f"Analyst LLM call failed: {exc}") from exc

    try:
        analysis = parse_json_to_model(raw_text, AnalysisResult)
    except ValueError as exc:
        log_error(
            "analysis_parse_failed",
            exc,
            raw_preview=raw_text[:500],
            attempt=attempt,
        )
        raise ValueError(f"Analyst returned invalid structured output: {exc}") from exc

    analysis = _normalize_analysis(
        analysis=analysis,
        research=research,
        plan=resolved_plan,
        attempt=attempt,
        improvement_notes=improvement_notes,
    )

    log_event(
        "analysis_completed",
        title=resolved_plan.title,
        attempt=attempt,
        key_findings=len(analysis.key_findings or []),
        trends=len(analysis.trends or []),
        open_gaps=len(analysis.open_gaps or []),
        section_insights=len(analysis.section_insights or {}),
    )
    return analysis


async def run_analyst_improvement(
    previous_analysis: AnalysisResult,
    validation: ValidationResult,
    agent: Optional[Any] = None,
) -> AnalysisResult:
    """
    Re-run analysis using prior research/plan plus validator feedback.
    Convenience wrapper for the validation retry loop.
    """
    if previous_analysis.research is None:
        raise ValueError(
            "previous_analysis.research is required for improvement retry."
        )
    if previous_analysis.plan is None:
        raise ValueError("previous_analysis.plan is required for improvement retry.")

    next_attempt = max(1, (previous_analysis.attempt or 1) + 1)
    return await run_analyst(
        research=previous_analysis.research,
        plan=previous_analysis.plan,
        validation_feedback=validation,
        attempt=next_attempt,
        agent=agent,
    )


def run_analyst_sync(
    research: ResearchResult,
    plan: Optional[ReportPlan] = None,
    validation_feedback: Optional[ValidationResult] = None,
    attempt: int = 1,
    agent: Optional[Any] = None,
) -> AnalysisResult:
    """Synchronous wrapper for scripts/tests."""
    import asyncio

    return asyncio.run(
        run_analyst(
            research=research,
            plan=plan,
            validation_feedback=validation_feedback,
            attempt=attempt,
            agent=agent,
        )
    )