"""HTTP routes for PhoenixRepo Pro. The existing /judge route is unchanged."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from config.settings import Settings
from pro.injection import neutralise
from pro.service import ProError, ProService

ROOT = Path(__file__).resolve().parents[1]
UI_PATH = Path(__file__).resolve().parent / "ui" / "index.html"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "injected_readme.md"

router = APIRouter()
SERVICE: ProService | None = None


class RunBody(BaseModel):
    url: str
    prefer_live: bool = False


def get_service() -> ProService:
    global SERVICE
    if SERVICE is None:
        directory = Path(os.environ.get("PHOENIX_PRO_DIR") or (ROOT / "data" / "pro-runtime"))
        SERVICE = ProService(directory, settings_fn=Settings)
    return SERVICE


def _ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded.strip():
        return forwarded.split(",")[0].strip()[:64] or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _error(exc: ProError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"error": {"type": exc.kind, "message": str(exc)}})


def ui_response():
    return FileResponse(UI_PATH, media_type="text/html; charset=utf-8")


@router.get("/pro")
@router.get("/pro/r/{result_id}")
@router.get("/pro/r/{result_id}/rescue")
@router.get("/pro/r/{result_id}/trace")
def pro_page(result_id: str = ""):
    return ui_response()


@router.get("/pro/api/status")
def pro_status():
    return get_service().status()


@router.get("/pro/api/examples")
def pro_examples():
    return {"examples": get_service().examples.cards()}


@router.get("/pro/api/examples/{example_id}")
@router.get("/pro/api/results/{example_id}")
def pro_result(example_id: str):
    try:
        return get_service().get_result(example_id)
    except ProError as exc:
        return _error(exc)


@router.post("/pro/api/runs")
def pro_start(body: RunBody, request: Request):
    try:
        return get_service().submit(body.url, _ip(request), prefer_live=body.prefer_live)
    except ProError as exc:
        return _error(exc)


@router.get("/pro/api/runs/{job_id}")
def pro_job(job_id: str):
    try:
        return get_service().get_job(job_id)
    except ProError as exc:
        return _error(exc)


@router.get("/pro/api/runs/{job_id}/events")
def pro_events(job_id: str):
    service = get_service()
    if job_id not in service.jobs:
        return JSONResponse(status_code=404, content={"error": {"type": "NotFound", "message": "Unknown run."}})

    def generate():
        for event in service.iter_events(job_id):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/pro/api/injection-demo")
def injection_demo():
    text = FIXTURE.read_text(encoding="utf-8")
    cleaned, report = neutralise(text)
    return {"original": text, "neutralised": cleaned, "report": report}
