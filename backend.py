# backend.py
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import logging
import os

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

import threading
import time
from collections import defaultdict, deque

from typing import Any, Dict

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import joblib
import datetime
import json
from pathlib import Path
import csv

from arena_routing import decide_arena
from case_intelligence import DEMO_CASES, run_case_intelligence
from case_intelligence.documents import UnsupportedDocument, extract_document_text
from case_report_pdf import build_case_report_pdf
from case_simulator import run_case_simulation
from rag_pipeline import hybrid_retriever, llm, judge_llm, rag_with_context
from translation import translate_text, translate_to_english

app = FastAPI()

# Allow frontend (HTML) to call this API. Override with a comma-separated
# ALLOWED_ORIGINS env var when deploying somewhere other than localhost.
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000"
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- Basic per-IP rate limiting for /chat (protects the paid Groq API key) ----

class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: float):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window_seconds:
                hits.popleft()
            if len(hits) >= self.max_requests:
                return False
            hits.append(now)
            return True


RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "20"))
rate_limiter = RateLimiter(max_requests=RATE_LIMIT_PER_MINUTE, window_seconds=60)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # These endpoints trigger paid LLM calls (or CPU-heavy work), so they
    # share the per-IP limit.
    if request.url.path in (
        "/chat", "/translate", "/simulate-case", "/simulate-case/pdf",
        "/case-intelligence", "/extract-document",
    ):
        client_ip = request.client.host if request.client else "unknown"
        if not rate_limiter.allow(client_ip):
            logger.warning(f"Rate limit exceeded for {client_ip}")
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests, please slow down."},
            )
    return await call_next(request)


# ---- Arena classifier & routing ----

MODEL_PATH = Path(__file__).parent / "models" / "arena_classifier.joblib"
arena_classifier = joblib.load(MODEL_PATH)


def predict_arena_auto(question: str) -> str:
    return arena_classifier.predict([question])[0]


def judge_against_context(question: str, answer: str, context: str):
    prompt = f"""You are a STRICT evaluator for a legal RAG system for Nepali law.

Question:
{question}

Retrieved legal context (from official Nepali law PDFs):
{context}

Assistant's answer:
{answer}

Evaluate:

1. correctness (0-2):
   - 0: Answer is wrong, irrelevant, or mostly does not address the question.
   - 1: Answer is partially correct but misses important details.
   - 2: Answer correctly addresses the question in most important aspects.

2. faithfulness (0-2):
   - 0: Answer contradicts the context or clearly uses details not present in the context.
   - 1: Answer is somewhat supported by the context, but parts are vague or appear invented.
   - 2: Answer is strongly and clearly supported by the context and does NOT add new legal claims.

Be strict and DO NOT give 2 unless you are confident.

Return ONLY a JSON object, no explanation, like:
{{"correctness": 1, "faithfulness": 2}}
"""
    resp = judge_llm.invoke(prompt)
    try:
        scores = json.loads(resp.content)
    except Exception:
        logger.warning(f"Judge returned non-JSON output: {resp.content!r}")
        scores = {"correctness": 0, "faithfulness": 0}
    return scores


# ---- Request/Response models ----

class ChatRequest(BaseModel):
    message: str
    arena: str = "All (auto)"


class ChatResponse(BaseModel):
    answer: str
    detected_arena: str
    ok: bool = True


@app.get("/health")
def health():
    return {"status": "ok"}


_csv_lock = threading.Lock()


def log_evaluation(question: str, detected_arena: str, answer: str, context: str) -> None:
    """Score the answer against its context and append a row to the CSV log.
    Runs after the response is built; failures here must never affect the
    chat response itself."""
    try:
        csv_path = Path("data/rag_evaluation_log_ui.csv")
        csv_path.parent.mkdir(parents=True, exist_ok=True)

        logger.debug(f"Judge input - question: {question!r}, answer[:200]: {answer[:200]!r}")

        if context:
            scores = judge_against_context(question, answer, context)
            correctness = scores.get("correctness", 0)
            faithfulness = scores.get("faithfulness", 0)
        else:
            correctness = 0
            faithfulness = 0

        ts = datetime.datetime.now().isoformat(timespec="seconds")
        row = [ts, question, detected_arena, answer, context, correctness, faithfulness]

        with _csv_lock:
            file_exists = csv_path.exists()
            with csv_path.open("a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow([
                        "timestamp", "question", "arena_used", "answer",
                        "context", "correctness", "faithfulness",
                    ])
                writer.writerow(row)

    except Exception:
        logger.exception("Eval logging error")


@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(req: ChatRequest):
    fallback_answer = "माफ गर्नुहोस्, सिस्टममा एउटा समस्या आयो, कृपया पछि पुनः प्रयास गर्नुहोस्।"

    try:
        detected_arena = decide_arena(req.message, req.arena, classify_fn=predict_arena_auto)

        # Use rag_with_context so we have context for evaluation.
        # `hybrid_retriever` fuses dense + BM25 (or falls back to dense-only
        # when hybrid retrieval is disabled via config).
        answer, context = rag_with_context(
            req.message,
            hybrid_retriever,
            llm,
            top_k=6,
            arena=detected_arena,
        )

        answer_with_note = f"{answer}\n\n[Detected arena: {detected_arena}]"

        log_evaluation(req.message, detected_arena, answer, context)

        return ChatResponse(answer=answer_with_note, detected_arena=detected_arena, ok=True)

    except Exception:
        logger.exception(f"chat_endpoint error for message={req.message!r}")
        return ChatResponse(answer=fallback_answer, detected_arena="All (auto)", ok=False)


# ---- Translation (bilingual answers) ----
# Pure post-processing of an already-generated answer: no retrieval, no new
# answer generation. See translation.py.

class TranslateRequest(BaseModel):
    text: str
    # Optional target language ('en' default keeps the original behaviour;
    # 'ne' powers the Case Intelligence bilingual toggle). Backward-compatible.
    target_lang: str = "en"


class TranslateResponse(BaseModel):
    translated_text: str


@app.post("/translate", response_model=TranslateResponse)
def translate_endpoint(req: TranslateRequest):
    text = (req.text or "").strip()
    target_lang = (req.target_lang or "en").strip().lower()
    if not text:
        raise HTTPException(status_code=422, detail="text must not be empty")
    if target_lang not in ("en", "ne"):
        raise HTTPException(status_code=422, detail="target_lang must be 'en' or 'ne'")
    if len(text) > 12000:
        raise HTTPException(status_code=413, detail="text too long to translate")

    try:
        translated = translate_text(text, target_lang=target_lang)
    except Exception:
        logger.exception("translate_endpoint error (%d chars)", len(text))
        raise HTTPException(status_code=502, detail="Translation service failed")

    return TranslateResponse(translated_text=translated)


# ---- Legal Case Simulator (educational multi-agent analysis) ----
# Case Input -> Analyzer -> Prosecution -> Defense -> Evidence Review -> Judge.
# See case_simulator.py; the workflow is independent of the RAG pipeline.

class CaseSimRequest(BaseModel):
    title: str
    description: str
    jurisdiction: str = "Nepal"
    case_type: str = "Other"


@app.post("/simulate-case")
def simulate_case_endpoint(req: CaseSimRequest):
    title = (req.title or "").strip()
    description = (req.description or "").strip()
    jurisdiction = (req.jurisdiction or "").strip() or "Nepal"
    case_type = (req.case_type or "").strip() or "Other"

    if not (3 <= len(title) <= 200):
        raise HTTPException(status_code=422, detail="title must be 3-200 characters")
    if len(description) < 30:
        raise HTTPException(
            status_code=422,
            detail="please describe the case scenario in at least 30 characters",
        )
    if len(description) > 8000:
        raise HTTPException(status_code=413, detail="case description too long (max 8000 characters)")

    try:
        report = run_case_simulation({
            "title": title,
            "description": description,
            "jurisdiction": jurisdiction[:100],
            "case_type": case_type[:50],
        })
    except Exception:
        logger.exception("simulate_case_endpoint error for title=%r", title)
        raise HTTPException(status_code=502, detail="Case simulation failed")

    return report


@app.post("/simulate-case/pdf")
def simulate_case_pdf_endpoint(report: Dict[str, Any] = Body(...)):
    """Render an already-generated simulation report to PDF.

    Takes the report JSON the client received from /simulate-case — agents are
    never re-run for an export.
    """
    if not isinstance(report, dict) or "case" not in report or "judge" not in report:
        raise HTTPException(status_code=422, detail="body must be a case simulation report")

    try:
        pdf_bytes = build_case_report_pdf(report)
    except Exception:
        logger.exception("simulate_case_pdf_endpoint error")
        raise HTTPException(status_code=500, detail="PDF generation failed")

    title = str(report.get("case", {}).get("title", "case"))
    safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title).strip()[:60] or "case"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name} - simulation report.pdf"'},
    )


@app.get("/case-simulator", response_class=HTMLResponse)
def case_simulator_page():
    page_path = Path(__file__).parent / "case-simulator.html"
    return HTMLResponse(content=page_path.read_text(encoding="utf-8"))


# ---- Legal Case Intelligence Suite (multi-agent, RAG-grounded) ----
# Case Analyzer -> Evidence -> Legal Research (reuses the RAG retriever) ->
# Prosecutor -> Defense -> Judge -> Verdict, plus a Timeline generator.
# See the case_intelligence package.

class CaseIntelRequest(BaseModel):
    title: str
    description: str
    jurisdiction: str = "Nepal"
    case_type: str = "Other"


@app.get("/case-intelligence/demos")
def case_intelligence_demos():
    """The educational demo scenarios (Feature 12)."""
    return {"cases": DEMO_CASES}


@app.post("/extract-document")
async def extract_document_endpoint(file: UploadFile = File(...)):
    """Extract plain text from an uploaded PDF/DOCX/TXT case file (Feature 1).

    Returns the text so the user can review/edit it before running analysis.
    No LLM is involved.
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="uploaded file is empty")
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large (max 10 MB)")

    try:
        text, kind = extract_document_text(file.filename or "", file.content_type or "", data)
    except UnsupportedDocument as exc:
        raise HTTPException(status_code=415, detail=str(exc))
    except Exception:
        logger.exception("extract_document_endpoint error for %r", file.filename)
        raise HTTPException(status_code=422, detail="could not extract text from this file")

    if not text.strip():
        raise HTTPException(status_code=422, detail="no readable text found in the document")
    return {"text": text, "kind": kind, "filename": file.filename, "chars": len(text)}


@app.post("/case-intelligence")
def case_intelligence_endpoint(req: CaseIntelRequest):
    """Run the full multi-agent Case Intelligence Suite on a case."""
    title = (req.title or "").strip()
    description = (req.description or "").strip()
    jurisdiction = (req.jurisdiction or "").strip() or "Nepal"
    case_type = (req.case_type or "").strip() or "Other"

    if not (3 <= len(title) <= 200):
        raise HTTPException(status_code=422, detail="title must be 3-200 characters")
    if len(description) < 30:
        raise HTTPException(status_code=422, detail="please describe the case in at least 30 characters")
    if len(description) > 12000:
        raise HTTPException(status_code=413, detail="case description too long (max 12000 characters)")

    try:
        report = run_case_intelligence({
            "title": title,
            "description": description,
            "jurisdiction": jurisdiction[:100],
            "case_type": case_type[:50],
        })
    except Exception:
        logger.exception("case_intelligence_endpoint error for title=%r", title)
        raise HTTPException(status_code=502, detail="Case intelligence analysis failed")

    return report


@app.get("/case-intelligence", response_class=HTMLResponse)
def case_intelligence_page():
    page_path = Path(__file__).parent / "case-intelligence.html"
    return HTMLResponse(content=page_path.read_text(encoding="utf-8"))


@app.get("/", response_class=HTMLResponse)
def root():
    index_path = Path(__file__).parent / "index.html"
    return HTMLResponse(content=index_path.read_text(encoding="utf-8"))


# Serve the frontend's static component assets (ES modules, etc.). This is
# purely additive — it does not alter the /chat, /health, or / API contracts.
_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
