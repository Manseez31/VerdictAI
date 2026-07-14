"""Smoke test only. Importing `backend` loads the embedding model, connects
to the Chroma vector store, and requires a valid GROQ_API_KEY, so this is
slow and needs a configured .env - it does not call the LLM itself."""

from fastapi.testclient import TestClient

from backend import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_root_serves_html():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_translate_endpoint(monkeypatch):
    """Contract test for POST /translate (LLM faked — no network call)."""
    import backend as backend_module

    monkeypatch.setattr(
        backend_module, "translate_text",
        lambda text, target_lang="en": "The council is established. [स्रोत: Nepal Pharmacy Council Act, 2057, धारा 3]",
    )
    resp = client.post("/translate", json={"text": "परिषद्को स्थापना। [स्रोत: Nepal Pharmacy Council Act, 2057, धारा 3]"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"translated_text"}
    assert "[स्रोत: Nepal Pharmacy Council Act, 2057, धारा 3]" in body["translated_text"]


def test_translate_endpoint_rejects_empty():
    resp = client.post("/translate", json={"text": "   "})
    assert resp.status_code == 422


def test_translate_endpoint_maps_failures_to_502(monkeypatch):
    import backend as backend_module

    def boom(text, target_lang="en"):
        raise RuntimeError("model down")

    monkeypatch.setattr(backend_module, "translate_text", boom)
    resp = client.post("/translate", json={"text": "केही पाठ"})
    assert resp.status_code == 502


# ---- Legal Case Simulator endpoints ----

_VALID_CASE = {
    "title": "Fake Investment Scheme",
    "description": "A promised investors 20% monthly returns and paid old investors with new deposits until the scheme collapsed.",
    "jurisdiction": "Nepal",
    "case_type": "Fraud",
}

_FAKE_REPORT = {
    "case": _VALID_CASE,
    "analysis": {"facts": ["f"], "reasoning_summary": "s", "laws_referenced": [], "confidence": 70},
    "prosecution": {"arguments": ["a"], "confidence": 70},
    "defense": {"arguments": ["d"], "confidence": 60},
    "evidence_review": {"overall_strength": "Moderate", "confidence": 65},
    "judge": {"verdict": "Uncertain Outcome", "confidence": 55},
    "disclaimer": "educational",
    "generated_at": "2026-07-13T20:00:00",
}


def test_simulate_case_endpoint(monkeypatch):
    import backend as backend_module

    monkeypatch.setattr(backend_module, "run_case_simulation", lambda case: _FAKE_REPORT)
    resp = client.post("/simulate-case", json=_VALID_CASE)
    assert resp.status_code == 200
    body = resp.json()
    assert body["judge"]["verdict"] == "Uncertain Outcome"
    assert "disclaimer" in body


def test_simulate_case_rejects_short_description():
    resp = client.post("/simulate-case", json={**_VALID_CASE, "description": "too short"})
    assert resp.status_code == 422


def test_simulate_case_maps_failures_to_502(monkeypatch):
    import backend as backend_module

    def boom(case):
        raise RuntimeError("agents down")

    monkeypatch.setattr(backend_module, "run_case_simulation", boom)
    resp = client.post("/simulate-case", json=_VALID_CASE)
    assert resp.status_code == 502


def test_simulate_case_pdf_endpoint():
    resp = client.post("/simulate-case/pdf", json=_FAKE_REPORT)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")


def test_simulate_case_pdf_rejects_non_report():
    resp = client.post("/simulate-case/pdf", json={"foo": "bar"})
    assert resp.status_code == 422


def test_case_simulator_page_served():
    resp = client.get("/case-simulator")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


# ---- Translation: bidirectional target_lang (backward compatible) ----

def test_translate_target_lang_nepali(monkeypatch):
    import backend as backend_module

    captured = {}

    def fake_translate(text, target_lang="en"):
        captured["target"] = target_lang
        return "अनुवादित पाठ"

    monkeypatch.setattr(backend_module, "translate_text", fake_translate)
    resp = client.post("/translate", json={"text": "hello", "target_lang": "ne"})
    assert resp.status_code == 200
    assert captured["target"] == "ne"


def test_translate_rejects_bad_target_lang():
    resp = client.post("/translate", json={"text": "hi", "target_lang": "fr"})
    assert resp.status_code == 422


# ---- Case Intelligence Suite endpoints ----

def test_case_intelligence_demos():
    resp = client.get("/case-intelligence/demos")
    assert resp.status_code == 200
    cases = resp.json()["cases"]
    assert len(cases) == 6
    assert all({"id", "title", "description", "case_type"} <= set(c) for c in cases)


def test_case_intelligence_run(monkeypatch):
    import backend as backend_module

    fake_report = {
        "case": _VALID_CASE, "analysis": {}, "timeline": {"events": []},
        "evidence": {}, "research": {"citations": []}, "prosecution": {},
        "defense": {}, "judge": {}, "verdict": {"likely_outcome": "Uncertain Outcome"},
        "disclaimer": "educational",
    }
    monkeypatch.setattr(backend_module, "run_case_intelligence", lambda case: fake_report)
    resp = client.post("/case-intelligence", json=_VALID_CASE)
    assert resp.status_code == 200
    assert resp.json()["verdict"]["likely_outcome"] == "Uncertain Outcome"


def test_case_intelligence_rejects_short_description():
    resp = client.post("/case-intelligence", json={**_VALID_CASE, "description": "short"})
    assert resp.status_code == 422


def test_case_intelligence_maps_failures_to_502(monkeypatch):
    import backend as backend_module

    def boom(case):
        raise RuntimeError("agents down")

    monkeypatch.setattr(backend_module, "run_case_intelligence", boom)
    resp = client.post("/case-intelligence", json=_VALID_CASE)
    assert resp.status_code == 502


def test_case_intelligence_page_served():
    resp = client.get("/case-intelligence")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_extract_document_txt():
    resp = client.post(
        "/extract-document",
        files={"file": ("case.txt", b"Party A signed an agreement with Party B.", "text/plain")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "txt"
    assert "Party A" in body["text"]


def test_extract_document_rejects_unsupported():
    resp = client.post(
        "/extract-document",
        files={"file": ("malware.exe", b"MZ\x90\x00", "application/octet-stream")},
    )
    assert resp.status_code == 415
