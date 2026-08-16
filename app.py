"""
app.py
------
FastAPI entrypoint for the AI Report Generation System.

Endpoints:
  GET  /health
  POST /generate-report
  GET  /docs  (Swagger UI — automatic)
"""

from __future__ import annotations

import traceback
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings
from models import (
    ErrorResponse,
    FileOutputs,
    GenerateReportResponse,
    HealthResponse,
    UserRequest,
    WorkflowStatus,
)
from report_generator import generate_report_files
from utils import get_logger, log_error, log_event, new_request_id, setup_logging
from workflow import WorkflowRunResult, run_report_workflow

# -----------------------------------------------------------------------------
# Logging / lifespan
# -----------------------------------------------------------------------------

setup_logging(settings.log_level)
logger = get_logger("ai_report_generator.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hooks."""
    settings.ensure_output_dir()
    log_event(
        "app_startup",
        provider=settings.llm_provider.value,
        model=settings.active_model_name,
        output_dir=settings.output_dir,
        max_validation_retries=settings.max_validation_retries,
    )
    yield
    log_event("app_shutdown")


app = FastAPI(
    title="AI Report Generation System",
    description=(
        "Generate professional reports from a topic/query using a multi-agent "
        "Microsoft Agent Framework workflow (Analyze → Plan → Research → "
        "Analyze → Validate → Write → Export)."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _result_to_response(
    result: WorkflowRunResult,
    files: Optional[FileOutputs] = None,
) -> GenerateReportResponse:
    """Map workflow outcome → API response model."""
    if result.success and result.document is not None:
        doc = result.document
        return GenerateReportResponse(
            status=WorkflowStatus.SUCCESS,
            title=doc.title,
            report=doc.content_markdown,
            files=files,
            validation_score=doc.validation_score,
            retries_used=result.retries_used,
            metadata={
                "request_id": result.request_id,
                "sections": doc.sections_included,
                "references_count": len(doc.references or []),
                "original_query": doc.original_query,
            },
        )

    # Failure paths
    status = WorkflowStatus.ERROR
    error_msg = result.error_message or "Report generation failed."
    meta: Dict[str, Any] = {"request_id": result.request_id}

    if result.failure is not None:
        status = result.failure.status
        error_msg = result.failure.message
        meta["stage"] = result.failure.stage
        if result.failure.details:
            meta["details"] = result.failure.details

    if result.validation is not None:
        meta["validation_score"] = result.validation.score
        meta["validation_status"] = result.validation.status.value
        meta["issues"] = result.validation.issues
        meta["missing_information"] = result.validation.missing_information
        meta["recommendations"] = result.validation.recommendations

    if result.plan is not None:
        meta["planned_title"] = result.plan.title

    return GenerateReportResponse(
        status=status,
        title=result.plan.title if result.plan else None,
        report=None,
        files=files,
        validation_score=(
            result.validation.score if result.validation is not None else None
        ),
        retries_used=result.retries_used,
        error=error_msg,
        metadata=meta,
    )


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """Liveness / basic config probe (does not call the LLM)."""
    return HealthResponse(
        status="ok",
        provider=settings.llm_provider.value,
        model=settings.active_model_name,
    )


@app.post(
    "/generate-report",
    response_model=GenerateReportResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    tags=["reports"],
)
async def generate_report(body: UserRequest) -> GenerateReportResponse:
    """
    Run the full multi-agent workflow and export report files.

    Request body example:
    {
      "query": "Generate a detailed report on the impact of AI in healthcare",
      "output_formats": ["md", "pdf", "docx"]
    }
    """
    request_id = new_request_id()
    query_preview = (body.query or "")[:120]

    log_event(
        "request_received",
        request_id=request_id,
        query_preview=query_preview,
        output_formats=body.output_formats,
    )

    # ---- validate input early ----
    if not body.query or not body.query.strip():
        log_error("request_empty_query", "empty query", request_id=request_id)
        raise HTTPException(
            status_code=400,
            detail="Query must not be empty.",
        )

    formats: Optional[List[str]] = body.output_formats
    if not formats:
        formats = settings.output_formats_list

    try:
        result = await run_report_workflow(
            body,
            output_formats=formats,
            request_id=request_id,
        )
    except Exception as exc:  # noqa: BLE001
        log_error("request_workflow_exception", exc, request_id=request_id)
        logger.debug("traceback: %s", traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                status=WorkflowStatus.ERROR,
                error="Unexpected error while generating the report.",
                detail=str(exc),
            ).model_dump(mode="json"),
        )

    files: Optional[FileOutputs] = None

    # Export only on success
    if result.success and result.document is not None:
        try:
            files = generate_report_files(
                document=result.document,
                formats=formats,
                output_dir=settings.output_dir,
                request_id=request_id,
            )
            log_event(
                "final_report_created",
                request_id=request_id,
                title=result.document.title,
                files=files.model_dump(exclude_none=True),
            )
        except Exception as exc:  # noqa: BLE001
            # Content succeeded; export failed — still return markdown in JSON
            log_error("request_export_failed", exc, request_id=request_id)
            files = None
            response = _result_to_response(result, files=None)
            response.metadata = {
                **(response.metadata or {}),
                "export_error": str(exc),
            }
            return response

    response = _result_to_response(result, files=files)

    if not result.success:
        # Business/validation failure → 200 with status field, or 422 for validation
        log_event(
            "request_completed_with_failure",
            request_id=request_id,
            status=response.status.value,
            error=response.error,
        )
    else:
        log_event(
            "request_completed_success",
            request_id=request_id,
            title=response.title,
            retries_used=response.retries_used,
        )

    return response


@app.get("/", tags=["system"])
async def root() -> Dict[str, Any]:
    """Simple landing payload."""
    return {
        "name": "AI Report Generation System",
        "status": "running",
        "provider": settings.llm_provider.value,
        "model": settings.active_model_name,
        "endpoints": {
            "health": "GET /health",
            "generate_report": "POST /generate-report",
            "docs": "GET /docs",
        },
    }


# -----------------------------------------------------------------------------
# Local dev entry
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
    )