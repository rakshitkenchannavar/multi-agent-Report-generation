"""
streamlit_app.py
----------------
Simple UI: enter a query → generate report → download files.
"""

import asyncio
from pathlib import Path

import streamlit as st

from backend.config import settings
from backend.models import UserRequest
from backend.report_generator import generate_report_files
from backend.utils import new_request_id, setup_logging
from backend.workflow import run_report_workflow

setup_logging(settings.log_level)

st.set_page_config(
    page_title="AI Report Generator",
    page_icon="📄",
    layout="centered",
)

st.title("📄 AI Report Generator")
st.caption("Enter a topic or question. The system will research, analyze, and write a report.")

# ----- Input -----
query = st.text_area(
    "Your query",
    height=120,
    placeholder="Example: Generate a detailed report on the impact of AI in healthcare",
)

formats = st.multiselect(
    "Output formats",
    options=["md", "pdf", "docx"],
    default=["md", "pdf", "docx"],
)

generate = st.button("Generate Report", type="primary", use_container_width=True)


def _run_async(coro):
    """Run async workflow from Streamlit."""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


if generate:
    if not query or not query.strip():
        st.error("Please enter a query.")
        st.stop()

    if not formats:
        st.error("Please select at least one output format.")
        st.stop()

    request_id = new_request_id()

    with st.spinner("Generating report... this may take a few minutes."):
        try:
            result = _run_async(
                run_report_workflow(
                    UserRequest(query=query.strip(), output_formats=formats),
                    output_formats=formats,
                    request_id=request_id,
                )
            )
        except Exception as exc:
            st.error(f"Something went wrong: {exc}")
            st.stop()

    if not result.success or result.document is None:
        st.error(result.error_message or "Report generation failed.")
        if result.validation is not None:
            st.write(f"**Validation score:** {result.validation.score}")
            if result.validation.issues:
                st.write("**Issues:**")
                for issue in result.validation.issues:
                    st.write(f"- {issue}")
        st.stop()

    doc = result.document
    st.success(f"Report ready: **{doc.title}**")

    if doc.validation_score is not None:
        st.info(f"Validation score: {doc.validation_score:.2f} · Retries used: {result.retries_used}")

    # --- RESEARCH SOURCE VERIFICATION ---
    sources = doc.references or []
    web_sources = [s for s in sources if s.source_type == "web"]
    llm_sources = [s for s in sources if s.source_type == "llm_knowledge"]

    if web_sources:
        st.info(f"🔍 **Live Web Search Used!** Found {len(web_sources)} real-time web sources via Tavily.")
    if llm_sources:
        st.warning(f"🧠 **AI Knowledge Used:** {len(llm_sources)} sources came from the AI's internal memory.")
    
    # Show markdown report
    st.subheader("Report")
    st.markdown(doc.content_markdown or "")

    # Export + download
    try:
        files = generate_report_files(
            document=doc,
            formats=formats,
            output_dir=settings.output_dir,
            request_id=request_id,
        )
    except Exception as exc:
        st.warning(f"Could not export files: {exc}")
        files = None

    if files:
        st.subheader("Download")
        cols = st.columns(3)

        if files.markdown:
            path = Path(files.markdown)
            if path.exists():
                cols[0].download_button(
                    "Download Markdown",
                    data=path.read_bytes(),
                    file_name=path.name,
                    mime="text/markdown",
                )

        if files.pdf:
            path = Path(files.pdf)
            if path.exists():
                cols[1].download_button(
                    "Download PDF",
                    data=path.read_bytes(),
                    file_name=path.name,
                    mime="application/pdf",
                )

        if files.docx:
            path = Path(files.docx)
            if path.exists():
                cols[2].download_button(
                    "Download DOCX",
                    data=path.read_bytes(),
                    file_name=path.name,
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )