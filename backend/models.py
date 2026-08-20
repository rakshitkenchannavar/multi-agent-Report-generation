"""
models.py
---------
Pydantic models for structured data exchanged between workflow stages.

Data flow:
    UserRequest
        → AnalyzedRequest
        → ReportPlan
        → ResearchResult
        → AnalysisResult
        → ValidationResult
        → ReportDocument
        → GenerateReportResponse
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator
# --- ADD THESE IMPORTS AT THE TOP OF models.py ---
from sqlmodel import SQLModel, Field as SQLField
from datetime import datetime, timezone

# --- ADD THESE DB & AUTH MODELS ---
class User(SQLModel, table=True):
    """DB Model for Users."""
    id: Optional[int] = SQLField(default=None, primary_key=True)
    username: str = SQLField(unique=True, index=True)
    email: str = SQLField(unique=True, index=True)
    hashed_password: str
    is_active: bool = SQLField(default=True)
    profile_picture_base64: Optional[str] = SQLField(default=None) # NEW FIELD
    created_at: datetime = SQLField(default_factory=lambda: datetime.now(timezone.utc))
    
class Report(SQLModel, table=True):
    """DB Model for saved reports."""
    id: Optional[int] = SQLField(default=None, primary_key=True)
    user_id: int = SQLField(foreign_key="user.id", index=True)
    title: str
    query: str
    markdown_content: str = SQLField(default="")
    pdf_path: Optional[str] = SQLField(default=None)
    docx_path: Optional[str] = SQLField(default=None)
    created_at: datetime = SQLField(default_factory=lambda: datetime.now(timezone.utc))

class UserRegister(BaseModel):
    username: str
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class TokenData(BaseModel):
    user_id: Optional[int] = None

# =============================================================================
# Enums
# =============================================================================


class ReportType(str, Enum):
    GENERAL = "general"
    TECHNICAL = "technical"
    BUSINESS = "business"
    RESEARCH = "research"
    COMPARISON = "comparison"
    ANALYTICAL = "analytical"


class DepthLevel(str, Enum):
    BRIEF = "brief"
    STANDARD = "standard"
    DETAILED = "detailed"
    COMPREHENSIVE = "comprehensive"


class ValidationStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"


class WorkflowStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    VALIDATION_FAILED = "validation_failed"
    ERROR = "error"


class OutputFormat(str, Enum):
    MARKDOWN = "md"
    PDF = "pdf"
    DOCX = "docx"


# =============================================================================
# API request / response
# =============================================================================


class UserRequest(BaseModel):
    """Incoming API request body for POST /generate-report."""

    query: str = Field(..., min_length=1, description="User topic or report request")
    output_formats: Optional[List[str]] = Field(
        default=None,
        description="Formats to generate: md, pdf, docx. Defaults to config values.",
    )

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Query must not be empty or whitespace only.")
        return cleaned

    @field_validator("output_formats", mode="before")
    @classmethod
    def normalize_formats(cls, v):
        if v is None:
            return v
        if isinstance(v, str):
            v = [v]
        return [str(item).strip().lower() for item in v if str(item).strip()]


class FileOutputs(BaseModel):
    """Paths (or URLs) of generated report files."""

    markdown: Optional[str] = None
    pdf: Optional[str] = None
    docx: Optional[str] = None


class GenerateReportResponse(BaseModel):
    """API response for POST /generate-report."""

    status: WorkflowStatus
    title: Optional[str] = None
    report: Optional[str] = None
    files: Optional[FileOutputs] = None
    validation_score: Optional[float] = None
    retries_used: int = 0
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str = "ok"
    provider: str
    model: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ErrorResponse(BaseModel):
    status: WorkflowStatus = WorkflowStatus.ERROR
    error: str
    detail: Optional[str] = None


# =============================================================================
# Stage 1 — Input Analyzer
# =============================================================================


class AnalyzedRequest(BaseModel):
    """Structured understanding of the user query."""

    original_query: str
    main_topic: str
    objective: str
    report_type: ReportType = ReportType.GENERAL
    depth: DepthLevel = DepthLevel.STANDARD
    key_requirements: List[str] = Field(default_factory=list)
    target_audience: Optional[str] = None
    constraints: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


# =============================================================================
# Stage 2 — Planner
# =============================================================================


class ReportSection(BaseModel):
    """One section (or subsection) in the planned report structure."""

    id: str = Field(..., description="Stable id, e.g. 'sec_intro'")
    title: str
    description: str = ""
    subsections: List["ReportSection"] = Field(default_factory=list)
    order: int = 0


class ResearchQuestion(BaseModel):
    """A concrete question the researcher should answer."""

    id: str
    question: str
    related_section_id: Optional[str] = None
    priority: int = Field(default=1, ge=1, le=5, description="1=highest, 5=lowest")


class ReportPlan(BaseModel):
    """Full plan produced by the Planning agent."""

    title: str
    executive_summary_required: bool = True
    sections: List[ReportSection] = Field(default_factory=list)
    research_questions: List[ResearchQuestion] = Field(default_factory=list)
    scope: str = ""
    assumptions: List[str] = Field(default_factory=list)
    success_criteria: List[str] = Field(default_factory=list)

    # Carry forward context the later stages still need
    analyzed_request: Optional[AnalyzedRequest] = None


# =============================================================================
# Stage 3 — Researcher
# =============================================================================


class SourceInfo(BaseModel):
    """Attribution for a piece of research."""

    title: Optional[str] = None
    url: Optional[str] = None
    source_type: str = Field(default="llm_knowledge", description="web | llm_knowledge | document | other")
    accessed_at: Optional[datetime] = None
    snippet: Optional[str] = None


class ResearchItem(BaseModel):
    """Answer / notes for one research question."""

    question_id: str
    question: str
    findings: str
    sources: List[SourceInfo] = Field(default_factory=list)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    related_section_id: Optional[str] = None


class ResearchResult(BaseModel):
    """Aggregated research output."""

    items: List[ResearchItem] = Field(default_factory=list)
    additional_notes: Optional[str] = None
    plan: Optional[ReportPlan] = None


# =============================================================================
# Stage 4 — Analyst
# =============================================================================


class KeyFinding(BaseModel):
    """A single interpreted finding (not raw research copy)."""

    title: str
    description: str
    importance: int = Field(default=3, ge=1, le=5)
    related_section_ids: List[str] = Field(default_factory=list)
    supporting_evidence: Optional[str] = None


class SWOTOrFactors(BaseModel):
    """Optional structured factors when relevant to the topic."""

    advantages: List[str] = Field(default_factory=list)
    disadvantages: List[str] = Field(default_factory=list)
    opportunities: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    """Interpreted analysis ready for validation / writing."""

    executive_overview: str = ""
    key_findings: List[KeyFinding] = Field(default_factory=list)
    trends: List[str] = Field(default_factory=list)
    comparisons: List[str] = Field(default_factory=list)
    factors: SWOTOrFactors = Field(default_factory=SWOTOrFactors)
    section_insights: Dict[str, str] = Field(
        default_factory=dict,
        description="Map of section_id → analytical insight text",
    )
    open_gaps: List[str] = Field(
        default_factory=list,
        description="Known gaps or weak areas in the analysis",
    )
    research: Optional[ResearchResult] = None
    plan: Optional[ReportPlan] = None

    # Filled when validator sends content back for improvement
    improvement_notes: Optional[str] = None
    attempt: int = 1


# =============================================================================
# Stage 5 — Validator
# =============================================================================


class ValidationResult(BaseModel):
    """Quality gate before report writing."""

    status: ValidationStatus
    score: float = Field(..., ge=0.0, le=1.0)
    issues: List[str] = Field(default_factory=list)
    missing_information: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    summary: Optional[str] = None

    # Pass-through for routing / next stages
    analysis: Optional[AnalysisResult] = None
    attempt: int = 1


# =============================================================================
# Stage 6 — Report Writer
# =============================================================================


class ReportDocument(BaseModel):
    """Final report content (not the physical file)."""

    title: str
    executive_summary: Optional[str] = None
    content_markdown: str = Field(..., description="Full report body in Markdown")
    references: List[SourceInfo] = Field(default_factory=list)
    sections_included: List[str] = Field(default_factory=list)

    # Context / audit
    original_query: Optional[str] = None
    validation_score: Optional[float] = None
    retries_used: int = 0
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# =============================================================================
# Workflow internal messages / failures
# =============================================================================


class WorkflowFailure(BaseModel):
    """Emitted when the pipeline cannot produce a valid report."""

    status: WorkflowStatus = WorkflowStatus.FAILED
    stage: str
    message: str
    details: Optional[str] = None
    validation: Optional[ValidationResult] = None
    retries_used: int = 0


class WorkflowContext(BaseModel):
    """
    Optional shared context object if you need a single bag of state.
    Prefer passing typed stage models; use this only for cross-cutting metadata.
    """

    request_id: str
    user_query: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    max_validation_retries: int = 2
    output_formats: List[str] = Field(default_factory=lambda: ["md"])
    metadata: Dict[str, Any] = Field(default_factory=dict)


# Resolve forward references (ReportSection.subsections)
ReportSection.model_rebuild()