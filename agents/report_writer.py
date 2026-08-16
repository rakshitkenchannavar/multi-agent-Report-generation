"""
agents/report_writer.py
-----------------------
Report Writer agent.

Responsibilities:
  - Receive validated analysis (plan + research + findings)
  - Generate the final professional report in Markdown
  - Follow the planned structure and logical flow
  - Include executive summary when required
  - Include references when sources are available
  - Return ReportDocument (content only — not PDF/DOCX files)
  
Business Summary: Writes the final, professional, human-readable report 
based on all the validated pieces. This is the voice of the system.
Logic: Takes the validated Analysis and the Plan. 
Instructs the LLM to write in professional Markdown, 
following the exact section structure, incorporating the key findings, 
and adding an executive summary. 
Strips any AI meta-commentary or code fences.
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional

from agents import create_chat_agent
from models import (
    AnalysisResult,
    ReportDocument,
    ReportPlan,
    SourceInfo,
    ValidationResult,
)
from prompts import REPORT_WRITER_SYSTEM, build_report_writer_prompt
from utils import (
    get_logger,
    log_error,
    log_event,
    message_content_to_str,
    utc_now,
)

logger = get_logger(__name__)

AGENT_NAME = "report_writer"

_CODE_FENCE_WRAP_RE = re.compile(
    r"^\s*```(?:markdown|md)?\s*\n([\s\S]*?)\n\s*```\s*$",
    re.IGNORECASE,
)


def get_report_writer_agent() -> Any:
    """Create the Report Writer agent."""
    return create_chat_agent(
        name=AGENT_NAME,
        instructions=REPORT_WRITER_SYSTEM,
    )


def _collect_sources(analysis: AnalysisResult) -> List[SourceInfo]:
    """Flatten unique sources from research items."""
    sources: List[SourceInfo] = []
    seen: set[str] = set()

    research = analysis.research
    if research is None:
        return sources

    for item in research.items or []:
        for src in item.sources or []:
            key = (src.url or "").strip().lower() or f"{src.title}|{src.snippet}"
            if key in seen:
                continue
            seen.add(key)
            sources.append(src)

    return sources


def _sources_json(sources: List[SourceInfo]) -> str:
    if not sources:
        return "[]"
    return json.dumps(
        [s.model_dump(mode="json", exclude_none=True) for s in sources],
        indent=2,
        default=str,
    )


def _plan_json(plan: ReportPlan) -> str:
    data = plan.model_dump(mode="json", exclude_none=True)
    return json.dumps(data, indent=2, default=str)


def _analysis_json(analysis: AnalysisResult) -> str:
    data = analysis.model_dump(
        mode="json",
        exclude={"research", "plan"},
        exclude_none=True,
    )
    return json.dumps(data, indent=2, default=str)


def _strip_outer_fence(markdown: str) -> str:
    """Remove accidental ```markdown ... ``` wrapping around the whole report."""
    text = (markdown or "").strip()
    match = _CODE_FENCE_WRAP_RE.match(text)
    if match:
        return match.group(1).strip()
    return text


def _extract_title(markdown: str, fallback: str) -> str:
    """Use first H1 if present; otherwise fallback plan title."""
    for line in (markdown or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or fallback
    return fallback


def _extract_executive_summary(markdown: str) -> Optional[str]:
    """Best-effort pull of an Executive Summary section body."""
    text = markdown or ""
    pattern = re.compile(
        r"^##\s+executive summary\s*\n([\s\S]*?)(?=^##\s+|\Z)",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(text)
    if not match:
        return None
    body = match.group(1).strip()
    return body or None


def _sections_included(plan: ReportPlan) -> List[str]:
    return [s.title for s in (plan.sections or []) if s.title]


def _original_query(plan: ReportPlan) -> Optional[str]:
    if plan.analyzed_request is not None:
        return plan.analyzed_request.original_query
    return None


def _normalize_document(
    content_markdown: str,
    plan: ReportPlan,
    analysis: AnalysisResult,
    validation: Optional[ValidationResult],
    sources: List[SourceInfo],
    retries_used: int,
) -> ReportDocument:
    markdown = _strip_outer_fence(content_markdown)
    if not markdown.strip():
        raise ValueError("Report Writer produced empty markdown content.")

    title = _extract_title(markdown, fallback=plan.title)
    exec_summary = _extract_executive_summary(markdown)
    if exec_summary is None and analysis.executive_overview:
        exec_summary = analysis.executive_overview

    score = None
    if validation is not None:
        score = validation.score

    return ReportDocument(
        title=title,
        executive_summary=exec_summary,
        content_markdown=markdown,
        references=sources,
        sections_included=_sections_included(plan),
        original_query=_original_query(plan),
        validation_score=score,
        retries_used=max(0, retries_used),
        generated_at=utc_now(),
    )


async def run_report_writer(
    analysis: AnalysisResult,
    validation: Optional[ValidationResult] = None,
    plan: Optional[ReportPlan] = None,
    retries_used: int = 0,
    agent: Optional[Any] = None,
) -> ReportDocument:
    """Generate the final Markdown report content."""
    if analysis is None:
        raise ValueError("AnalysisResult is required for report writing.")

    resolved_plan = plan or analysis.plan
    if resolved_plan is None:
        raise ValueError(
            "ReportPlan is required for report writing (pass plan or set analysis.plan)."
        )

    if validation is not None and validation.analysis is not None:
        analysis = validation.analysis
        if validation.analysis.plan is not None:
            resolved_plan = validation.analysis.plan

    sources = _collect_sources(analysis)
    original_query = _original_query(resolved_plan) or resolved_plan.title

    log_event(
        "report_writing_started",
        title=resolved_plan.title,
        retries_used=retries_used,
        sources=len(sources),
        validation_score=getattr(validation, "score", None),
    )

    user_prompt = build_report_writer_prompt(
        title=resolved_plan.title,
        executive_summary_required=bool(resolved_plan.executive_summary_required),
        original_query=original_query,
        plan_json=_plan_json(resolved_plan),
        analysis_json=_analysis_json(analysis),
        sources_json=_sources_json(sources),
    )

    chat_agent = agent or get_report_writer_agent()

    try:
        result = await chat_agent.run(user_prompt)
        raw_text = message_content_to_str(getattr(result, "text", None) or result)
    except Exception as exc:  # noqa: BLE001
        log_error("report_writing_llm_failed", exc)
        raise RuntimeError(f"Report Writer LLM call failed: {exc}") from exc

    try:
        document = _normalize_document(
            content_markdown=raw_text,
            plan=resolved_plan,
            analysis=analysis,
            validation=validation,
            sources=sources,
            retries_used=retries_used,
        )
    except ValueError as exc:
        log_error("report_writing_normalize_failed", exc, raw_preview=raw_text[:500])
        raise

    log_event(
        "report_writing_completed",
        title=document.title,
        chars=len(document.content_markdown or ""),
        sections=len(document.sections_included or []),
        references=len(document.references or []),
        validation_score=document.validation_score,
    )
    return document


def run_report_writer_sync(
    analysis: AnalysisResult,
    validation: Optional[ValidationResult] = None,
    plan: Optional[ReportPlan] = None,
    retries_used: int = 0,
    agent: Optional[Any] = None,
) -> ReportDocument:
    """Synchronous wrapper for scripts/tests."""
    import asyncio

    return asyncio.run(
        run_report_writer(
            analysis=analysis,
            validation=validation,
            plan=plan,
            retries_used=retries_used,
            agent=agent,
        )
    )