"""
workflow.py
-----------
Microsoft Agent Framework workflow orchestration.

Flow:
    User Query
        → Input Analyzer
        → Planner
        → Researcher
        → Analyst
        → Validator
            ├─ FAIL (retries left) → Analyst (improvement) → Validator
            ├─ FAIL (no retries)   → WorkflowFailure
            └─ PASS                → Report Writer
                                        → ReportDocument

Uses MAF Executors + WorkflowBuilder when available, with a reliable
async pipeline runner that always works for the FastAPI entrypoint.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, List, Optional, Union

from config import settings
from models import (
    AnalysisResult,
    AnalyzedRequest,
    ReportDocument,
    ReportPlan,
    ResearchResult,
    UserRequest,
    ValidationResult,
    ValidationStatus,
    WorkflowFailure,
    WorkflowStatus,
)
from utils import get_logger, log_error, log_event, new_request_id

logger = get_logger(__name__)


# =============================================================================
# Workflow result envelope
# =============================================================================


@dataclass
class WorkflowRunResult:
    """Outcome of a full report-generation workflow run."""

    request_id: str
    success: bool
    document: Optional[ReportDocument] = None
    failure: Optional[WorkflowFailure] = None
    analyzed: Optional[AnalyzedRequest] = None
    plan: Optional[ReportPlan] = None
    research: Optional[ResearchResult] = None
    analysis: Optional[AnalysisResult] = None
    validation: Optional[ValidationResult] = None
    retries_used: int = 0
    error_message: Optional[str] = None

    @property
    def status(self) -> WorkflowStatus:
        if self.success and self.document is not None:
            return WorkflowStatus.SUCCESS
        if self.failure is not None:
            return self.failure.status
        return WorkflowStatus.ERROR


# =============================================================================
# Shared per-run state (also used by MAF executors)
# =============================================================================


@dataclass
class _PipelineState:
    request_id: str
    query: str
    output_formats: List[str] = field(default_factory=list)
    max_retries: int = 2
    pass_score: float = 0.7

    analyzed: Optional[AnalyzedRequest] = None
    plan: Optional[ReportPlan] = None
    research: Optional[ResearchResult] = None
    analysis: Optional[AnalysisResult] = None
    validation: Optional[ValidationResult] = None
    document: Optional[ReportDocument] = None
    failure: Optional[WorkflowFailure] = None

    attempt: int = 1
    retries_used: int = 0


def _failure(
    stage: str,
    message: str,
    *,
    status: WorkflowStatus = WorkflowStatus.FAILED,
    details: Optional[str] = None,
    validation: Optional[ValidationResult] = None,
    retries_used: int = 0,
) -> WorkflowFailure:
    return WorkflowFailure(
        status=status,
        stage=stage,
        message=message,
        details=details,
        validation=validation,
        retries_used=retries_used,
    )


# =============================================================================
# Core async pipeline (primary path used by the API)
# =============================================================================


async def run_report_workflow(
    request: Union[UserRequest, str],
    *,
    output_formats: Optional[List[str]] = None,
    max_validation_retries: Optional[int] = None,
    request_id: Optional[str] = None,
) -> WorkflowRunResult:
    """
    Execute the full multi-agent report generation pipeline.

    This is the main entrypoint called from FastAPI.
    """
    # Lazy imports keep module import light and avoid circular refs
    from agents.analyst import run_analyst, run_analyst_improvement
    from agents.input_analyzer import run_input_analyzer
    from agents.planner import run_planner
    from agents.report_writer import run_report_writer
    from agents.researcher import run_researcher
    from agents.validator import run_validator

    rid = request_id or new_request_id()

    if isinstance(request, UserRequest):
        query = request.query.strip()
        formats = output_formats or request.output_formats or settings.output_formats_list
    else:
        query = (request or "").strip()
        formats = output_formats or settings.output_formats_list

    if not query:
        fail = _failure("input", "Query must not be empty.", status=WorkflowStatus.ERROR)
        log_error("workflow_invalid_input", fail.message, request_id=rid)
        return WorkflowRunResult(
            request_id=rid,
            success=False,
            failure=fail,
            error_message=fail.message,
        )

    max_retries = (
        max_validation_retries
        if max_validation_retries is not None
        else settings.max_validation_retries
    )
    pass_score = settings.validation_pass_score

    state = _PipelineState(
        request_id=rid,
        query=query,
        output_formats=[f.lower().strip() for f in formats if f],
        max_retries=max_retries,
        pass_score=pass_score,
    )

    log_event(
        "workflow_started",
        request_id=rid,
        query_length=len(query),
        max_retries=max_retries,
        formats=state.output_formats,
    )

    try:
        # ------------------------------------------------------------------
        # 1. Input Analyzer
        # ------------------------------------------------------------------
        state.analyzed = await run_input_analyzer(query)

        # ------------------------------------------------------------------
        # 2. Planner
        # ------------------------------------------------------------------
        state.plan = await run_planner(state.analyzed)

        # ------------------------------------------------------------------
        # 3. Researcher
        # ------------------------------------------------------------------
        state.research = await run_researcher(state.plan)

        # ------------------------------------------------------------------
        # 4–5. Analyst ↔ Validator loop
        # ------------------------------------------------------------------
        state.attempt = 1
        state.retries_used = 0
        state.analysis = await run_analyst(
            research=state.research,
            plan=state.plan,
            attempt=state.attempt,
        )
        state.validation = await run_validator(
            analysis=state.analysis,
            plan=state.plan,
            attempt=state.attempt,
            pass_score=pass_score,
            max_retries=max_retries,
        )

        while (
            state.validation.status != ValidationStatus.PASS
            and state.attempt <= max_retries
        ):
            # Consume a retry and improve analysis
            state.retries_used += 1
            state.attempt += 1

            log_event(
                "validation_retry_triggered",
                request_id=rid,
                attempt=state.attempt,
                retries_used=state.retries_used,
                score=state.validation.score,
                issues=len(state.validation.issues or []),
            )

            state.analysis = await run_analyst_improvement(
                previous_analysis=state.analysis,
                validation=state.validation,
            )
            # Keep attempt in sync on analysis
            state.analysis.attempt = state.attempt

            state.validation = await run_validator(
                analysis=state.analysis,
                plan=state.plan,
                attempt=state.attempt,
                pass_score=pass_score,
                max_retries=max_retries,
            )

        if state.validation.status != ValidationStatus.PASS:
            msg = (
                f"Content failed validation after {state.retries_used} retry(ies). "
                f"Final score={state.validation.score:.2f}."
            )
            state.failure = _failure(
                stage="validator",
                message=msg,
                status=WorkflowStatus.VALIDATION_FAILED,
                details=state.validation.summary
                or "; ".join(state.validation.issues or []),
                validation=state.validation,
                retries_used=state.retries_used,
            )
            log_event(
                "workflow_validation_failed",
                request_id=rid,
                score=state.validation.score,
                retries_used=state.retries_used,
            )
            return WorkflowRunResult(
                request_id=rid,
                success=False,
                failure=state.failure,
                analyzed=state.analyzed,
                plan=state.plan,
                research=state.research,
                analysis=state.analysis,
                validation=state.validation,
                retries_used=state.retries_used,
                error_message=msg,
            )

        # ------------------------------------------------------------------
        # 6. Report Writer
        # ------------------------------------------------------------------
        state.document = await run_report_writer(
            analysis=state.analysis,
            validation=state.validation,
            plan=state.plan,
            retries_used=state.retries_used,
        )

        log_event(
            "workflow_completed",
            request_id=rid,
            title=state.document.title,
            retries_used=state.retries_used,
            validation_score=state.validation.score,
            chars=len(state.document.content_markdown or ""),
        )

        return WorkflowRunResult(
            request_id=rid,
            success=True,
            document=state.document,
            analyzed=state.analyzed,
            plan=state.plan,
            research=state.research,
            analysis=state.analysis,
            validation=state.validation,
            retries_used=state.retries_used,
        )

    except ValueError as exc:
        log_error("workflow_value_error", exc, request_id=rid)
        fail = _failure(
            stage="workflow",
            message=str(exc),
            status=WorkflowStatus.ERROR,
            retries_used=state.retries_used,
        )
        return WorkflowRunResult(
            request_id=rid,
            success=False,
            failure=fail,
            analyzed=state.analyzed,
            plan=state.plan,
            research=state.research,
            analysis=state.analysis,
            validation=state.validation,
            retries_used=state.retries_used,
            error_message=str(exc),
        )
    except Exception as exc:  # noqa: BLE001
        log_error("workflow_failed", exc, request_id=rid)
        fail = _failure(
            stage="workflow",
            message="Report generation workflow failed.",
            status=WorkflowStatus.ERROR,
            details=str(exc),
            retries_used=state.retries_used,
        )
        return WorkflowRunResult(
            request_id=rid,
            success=False,
            failure=fail,
            analyzed=state.analyzed,
            plan=state.plan,
            research=state.research,
            analysis=state.analysis,
            validation=state.validation,
            retries_used=state.retries_used,
            error_message=str(exc),
        )


# =============================================================================
# MAF WorkflowBuilder graph (optional / advanced)
# =============================================================================


def build_maf_workflow() -> Any:
    """
    Build a Microsoft Agent Framework workflow graph.

    Mirrors the same stages as run_report_workflow().
    Prefer run_report_workflow() from the API for a stable response envelope;
    use this when you want native MAF orchestration / visualization.
    """
    try:
        from agent_framework import (
            Executor,
            WorkflowBuilder,
            WorkflowContext,
            handler,
        )
    except ImportError as exc:
        raise ImportError(
            "Microsoft Agent Framework is required for build_maf_workflow(). "
            "Install agent-framework / agent-framework-core."
        ) from exc

    from agents.analyst import run_analyst, run_analyst_improvement
    from agents.input_analyzer import run_input_analyzer
    from agents.planner import run_planner
    from agents.report_writer import run_report_writer
    from agents.researcher import run_researcher
    from agents.validator import run_validator

    # ----- Executors -----

    class InputAnalyzerExec(Executor):
        def __init__(self) -> None:
            super().__init__(id="input_analyzer")

        @handler
        async def run(self, request: UserRequest, ctx: WorkflowContext) -> None:
            log_event("maf_stage", stage="input_analyzer")
            analyzed = await run_input_analyzer(request)
            await ctx.send_message(analyzed)

    class PlannerExec(Executor):
        def __init__(self) -> None:
            super().__init__(id="planner")

        @handler
        async def run(self, analyzed: AnalyzedRequest, ctx: WorkflowContext) -> None:
            log_event("maf_stage", stage="planner")
            plan = await run_planner(analyzed)
            await ctx.send_message(plan)

    class ResearcherExec(Executor):
        def __init__(self) -> None:
            super().__init__(id="researcher")

        @handler
        async def run(self, plan: ReportPlan, ctx: WorkflowContext) -> None:
            log_event("maf_stage", stage="researcher")
            research = await run_researcher(plan)
            await ctx.send_message(research)

    class AnalystExec(Executor):
        def __init__(self) -> None:
            super().__init__(id="analyst")

        @handler
        async def run(
            self,
            data: Union[ResearchResult, ValidationResult],
            ctx: WorkflowContext,
        ) -> None:
            log_event("maf_stage", stage="analyst")
            if isinstance(data, ValidationResult):
                if data.analysis is None:
                    raise ValueError("ValidationResult.analysis required for retry.")
                analysis = await run_analyst_improvement(
                    previous_analysis=data.analysis,
                    validation=data,
                )
            else:
                analysis = await run_analyst(research=data, plan=data.plan, attempt=1)
            await ctx.send_message(analysis)

    class ValidatorExec(Executor):
        def __init__(self) -> None:
            super().__init__(id="validator")

        @handler
        async def run(self, analysis: AnalysisResult, ctx: WorkflowContext) -> None:
            log_event("maf_stage", stage="validator")
            validation = await run_validator(
                analysis=analysis,
                plan=analysis.plan,
                attempt=analysis.attempt,
                pass_score=settings.validation_pass_score,
                max_retries=settings.max_validation_retries,
            )
            await ctx.send_message(validation)

    class ReportWriterExec(Executor):
        def __init__(self) -> None:
            super().__init__(id="report_writer")

        @handler
        async def run(self, validation: ValidationResult, ctx: WorkflowContext) -> None:
            log_event("maf_stage", stage="report_writer")
            if validation.analysis is None:
                raise ValueError("ValidationResult.analysis is required to write report.")
            retries_used = max(0, (validation.attempt or 1) - 1)
            document = await run_report_writer(
                analysis=validation.analysis,
                validation=validation,
                plan=validation.analysis.plan,
                retries_used=retries_used,
            )
            await ctx.send_message(document)

    class ValidationFailedExec(Executor):
        def __init__(self) -> None:
            super().__init__(id="validation_failed")

        @handler
        async def run(self, validation: ValidationResult, ctx: WorkflowContext) -> None:
            log_event("maf_stage", stage="validation_failed")
            fail = _failure(
                stage="validator",
                message="Validation failed after maximum retries.",
                status=WorkflowStatus.VALIDATION_FAILED,
                details=validation.summary,
                validation=validation,
                retries_used=max(0, (validation.attempt or 1) - 1),
            )
            await ctx.send_message(fail)

    input_analyzer = InputAnalyzerExec()
    planner = PlannerExec()
    researcher = ResearcherExec()
    analyst = AnalystExec()
    validator = ValidatorExec()
    report_writer = ReportWriterExec()
    validation_failed = ValidationFailedExec()

    def _is_pass(v: ValidationResult) -> bool:
        return getattr(v, "status", None) == ValidationStatus.PASS

    def _should_retry(v: ValidationResult) -> bool:
        if _is_pass(v):
            return False
        attempt = getattr(v, "attempt", 1) or 1
        return attempt <= settings.max_validation_retries

    def _is_final_fail(v: ValidationResult) -> bool:
        return (not _is_pass(v)) and (not _should_retry(v))

    builder = (
        WorkflowBuilder()
        .set_start_executor(input_analyzer)
        .add_edge(input_analyzer, planner)
        .add_edge(planner, researcher)
        .add_edge(researcher, analyst)
        .add_edge(analyst, validator)
        .add_edge(validator, report_writer, condition=_is_pass)
        .add_edge(validator, analyst, condition=_should_retry)
        .add_edge(validator, validation_failed, condition=_is_final_fail)
    )

    workflow = builder.build()
    log_event("maf_workflow_built", max_retries=settings.max_validation_retries)
    return workflow


async def run_maf_workflow(request: Union[UserRequest, str]) -> Any:
    """
    Run the native MAF workflow graph.
    Returns the last workflow event/message (framework-specific).
    For API responses, prefer run_report_workflow().
    """
    if isinstance(request, str):
        request = UserRequest(query=request)

    workflow = build_maf_workflow()
    # MAF API surface varies slightly by version
    if hasattr(workflow, "run"):
        return await workflow.run(request)
    if hasattr(workflow, "run_async"):
        return await workflow.run_async(request)
    raise RuntimeError("MAF workflow object has no run/run_async method.")