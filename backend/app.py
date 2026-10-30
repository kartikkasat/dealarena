"""DealArena compute API (FastAPI).

Endpoints
  GET  /api/health                  service status
  GET  /api/model                   model card of the rival model: what exists today, what is planned
  GET  /api/scenario/default        the sample case
  POST /api/simulate                run a simulation; the system generates the bid
  POST /api/simulate/stress         same case with more aggressive rivals
  GET  /api/runs                    run ledger, newest first
  GET  /api/runs.csv                run ledger as CSV
  GET  /api/runs/{run_id}           full inputs and outputs of one run
  POST /api/runs/{run_id}/replay    re-run with the stored inputs and seed, and check it is identical
  POST /api/runs/{run_id}/decision  record the deal committee decision (approved or rejected) on a run
Interactive documentation: /docs
The web interface is served from the project root at /.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import engine
from .models import DecisionIn, RunSummary, Scenario, SimulationResponse
from .runlog import RunLedger, input_hash, setup_logging

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
log = setup_logging(LOG_DIR)
ledger = RunLedger(LOG_DIR)
STARTED = time.time()
API_VERSION = "1.1.0"   # engine computations are unchanged at engine.VERSION

app = FastAPI(
    title="DealArena compute API",
    version=API_VERSION,
    description="Rule-based Monte Carlo auction simulator with adaptive rivals for M&A bid decisions. "
                "Not a trained MARL model. All values in EUR millions; all data hypothetical.",
)


@app.middleware("http")
async def headers_and_timing(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if not request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'")
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        log.info("request", extra={"data": {"method": request.method, "path": request.url.path,
                                             "status": response.status_code,
                                             "ms": round((time.perf_counter() - t0) * 1000, 1)}})
    else:
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    msgs = []
    for e in exc.errors():
        where = ".".join(str(x) for x in e.get("loc", [])[1:])
        msgs.append(f"{where}: {e.get('msg')}" if where else e.get("msg"))
    log.warning("invalid input", extra={"data": {"path": request.url.path, "errors": msgs}})
    return JSONResponse(status_code=422, content={"detail": msgs})


def _clean(x):
    """Make engine output JSON-safe (NumPy scalars, NaN and infinity)."""
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, np.ndarray):
        return [_clean(v) for v in x.tolist()]
    if isinstance(x, (np.floating,)):
        x = float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def _simulate(scenario: dict, kind: str, seed: int | None = None, replay_of: str | None = None) -> dict:
    t0 = time.perf_counter()
    result = engine.run(scenario, seed=seed)
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result.get("errors", ["Invalid scenario"]))
    result = _clean(result)
    ms = round((time.perf_counter() - t0) * 1000)
    summary = ledger.record(kind, scenario, result, ms, replay_of=replay_of)
    log.info("simulation", extra={"data": {k: summary[k] for k in ("runId", "kind", "seed", "runs", "systemBid", "pWin", "maxBid", "durationMs", "warnings")}})
    meta = {"runId": summary["runId"], "kind": kind, "createdAt": summary["createdAt"], "seed": result["seed"],
            "inputHash": summary["inputHash"], "durationMs": ms, "replayOf": replay_of,
            "rivalModel": result.get("rivalModel", "")}
    return {"run": meta, "result": result}


@app.get("/api/health")
def health():
    return {"status": "ok", "engine": "rule-based Monte Carlo with adaptive rivals", "version": engine.VERSION,
            "apiVersion": API_VERSION, "rivalModel": engine.RIVAL_MODEL["id"],
            "uptimeS": round(time.time() - STARTED), "runsLogged": ledger.count(),
            "serverTime": datetime.now(timezone.utc).isoformat(timespec="seconds")}


@app.get("/api/model")
def model_card():
    """What the rival model is today and how it would become trained agents (data, calibration, training,
    validation, serving)."""
    return {"engineVersion": engine.VERSION, **engine.RIVAL_MODEL}


@app.get("/api/scenario/default")
def default_scenario():
    return engine.default_scenario()


@app.post("/api/simulate", response_model=SimulationResponse)
def simulate(scenario: Scenario):
    return _simulate(scenario.model_dump(), "simulate")


@app.post("/api/simulate/stress", response_model=SimulationResponse)
def simulate_stress(scenario: Scenario):
    """Same case with more aggressive rivals: fever +15 pp, fever premium +5 pp, bid shading +2 pp."""
    return _simulate(engine.stress_variant(scenario.model_dump()), "stress")


@app.get("/api/runs", response_model=list[RunSummary])
def list_runs(limit: int = Query(50, ge=1, le=500)):
    return ledger.list(limit)


@app.get("/api/runs.csv", response_class=PlainTextResponse)
def runs_csv():
    return PlainTextResponse(ledger.csv(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=dealarena-runs.csv"})


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    rec = ledger.get(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=["Run not found"])
    return rec


@app.post("/api/runs/{run_id}/replay")
def replay(run_id: str):
    rec = ledger.get(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=["Run not found"])
    out = _simulate(rec["inputs"], "replay", seed=rec["summary"]["seed"], replay_of=run_id)
    a, b = rec["result"].get("recommendation"), out["result"].get("recommendation")
    out["identical"] = (a is None and b is None) or (a is not None and b is not None and
                                                    abs(a["bid"] - b["bid"]) < 1e-9 and abs(a["pWin"] - b["pWin"]) < 1e-12)
    out["inputHashMatches"] = input_hash(rec["inputs"]) == rec["summary"]["inputHash"]
    return out


@app.post("/api/runs/{run_id}/decision")
def decide(run_id: str, body: DecisionIn):
    """Record the deal committee's decision on a logged run. One decision per run; it cannot be changed."""
    try:
        entry = ledger.decide(run_id, body.approver, body.decision, body.note)
    except LookupError:
        raise HTTPException(status_code=404, detail=["Run not found"])
    except ValueError as e:
        raise HTTPException(status_code=409, detail=[str(e)])
    log.info("decision", extra={"data": entry})
    return entry


# The web interface. Only these folders are served, never the backend code or the logs.
for _d in ("css", "js", "fonts", "vendor"):
    app.mount(f"/{_d}", StaticFiles(directory=str(ROOT / _d)), name=_d)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(ROOT / "index.html", media_type="text/html")
