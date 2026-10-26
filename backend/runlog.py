"""Run ledger and application logging.

Every simulation is written to:
- logs/runs.jsonl        one JSON line per run (summary, seed, input hash): the audit trail
- logs/runs/<id>.json    the full inputs and outputs of that run, so it can be inspected or replayed
- logs/decisions.jsonl   deal committee decisions (approver, approved or rejected, note), one per run at most
- logs/dealarena.log     structured application log (JSON lines, rotated)
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import logging.handlers
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _flatten(obj, prefix="", out=None):
    out = {} if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten(v, f"{prefix}.{k}" if prefix else k, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            key = v.get("name", i) if isinstance(v, dict) else i
            _flatten(v, f"{prefix}[{key}]", out)
    else:
        out[prefix] = obj
    return out


def describe_changes(prev: dict | None, cur: dict) -> str:
    """Short human-readable list of inputs that changed since the previous run."""
    if prev is None:
        return "First run"
    a, b = _flatten(prev), _flatten(cur)
    skip = ("sim.seed",)
    diffs = [f"{k}: {a.get(k)} to {b[k]}" for k in b if k not in skip and not k.endswith(".id") and a.get(k) != b[k]]
    diffs += [f"{k} removed" for k in a if k not in b and k.endswith(".name")]
    if not diffs:
        return "No input change"
    return "; ".join(diffs[:6]) + (f"; and {len(diffs) - 6} more" if len(diffs) > 6 else "")


def input_hash(scenario: dict) -> str:
    canon = json.dumps(scenario, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "level": record.levelname,
                   "logger": record.name, "msg": record.getMessage()}
        extra = getattr(record, "data", None)
        if extra:
            payload.update(extra)
        return json.dumps(payload, default=str)


def setup_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("dealarena")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fh = logging.handlers.RotatingFileHandler(log_dir / "dealarena.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(JsonFormatter())
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)s  %(message)s", "%H:%M:%S"))
    logger.addHandler(fh)
    logger.addHandler(sh)
    logger.propagate = False
    return logger


class RunLedger:
    def __init__(self, log_dir: Path):
        self.dir = log_dir
        self.runs_dir = log_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = log_dir / "runs.jsonl"
        self.decisions = log_dir / "decisions.jsonl"
        self.lock = threading.Lock()
        self.last_inputs: dict | None = None

    @staticmethod
    def new_id() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]

    def record(self, kind: str, scenario: dict, result: dict, duration_ms: int, replay_of: str | None = None) -> dict:
        run_id = self.new_id()
        created = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rec = result.get("recommendation")
        th = result.get("thresholds") or {}
        ad = result.get("adaptation") or {}
        with self.lock:
            changes = describe_changes(self.last_inputs, scenario) if kind == "simulate" else ""
            if kind == "simulate":
                self.last_inputs = scenario
            summary = {
                "runId": run_id, "kind": kind, "createdAt": created, "seed": result["seed"], "runs": result["runs"],
                "inputHash": input_hash(scenario), "durationMs": duration_ms,
                "systemBid": rec["bid"] if rec else None, "pWin": rec["pWin"] if rec else None,
                "maxBid": th.get("ceiling"), "adaptive": bool(ad.get("enabled")),
                "warnings": len(result.get("warnings", [])), "reliability": result.get("reliability", ""),
                "replayOf": replay_of, "changes": changes, "rivalModel": result.get("rivalModel", ""),
            }
            with self.ledger.open("a", encoding="utf-8") as f:
                f.write(json.dumps(summary) + "\n")
            (self.runs_dir / f"{run_id}.json").write_text(
                json.dumps({"summary": summary, "inputs": scenario, "result": result}, default=float), encoding="utf-8")
        return summary

    def _decisions(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        if self.decisions.exists():
            for ln in self.decisions.read_text(encoding="utf-8").splitlines():
                try:
                    d = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                out.setdefault(d["runId"], d)      # first decision stands; later lines cannot overwrite it
        return out

    def list(self, limit: int = 50) -> list[dict]:
        if not self.ledger.exists():
            return []
        with self.lock:
            lines = self.ledger.read_text(encoding="utf-8").splitlines()
            decided = self._decisions()
        out = []
        for ln in reversed(lines):
            try:
                row = json.loads(ln)
            except json.JSONDecodeError:
                continue
            row["decision"] = decided.get(row.get("runId"))
            out.append(row)
            if len(out) >= limit:
                break
        return out

    def get(self, run_id: str) -> dict | None:
        if not run_id.replace("-", "").isalnum():
            return None
        p = self.runs_dir / f"{run_id}.json"
        if not p.exists():
            return None
        rec = json.loads(p.read_text(encoding="utf-8"))
        with self.lock:
            rec["decision"] = self._decisions().get(run_id)
        return rec

    def decide(self, run_id: str, approver: str, decision: str, note: str) -> dict:
        """Record the committee decision on a run. Raises LookupError (unknown run) or ValueError (not allowed)."""
        rec = self.get(run_id)
        if rec is None:
            raise LookupError("Run not found")
        summ = rec["summary"]
        if rec.get("decision"):
            d = rec["decision"]
            raise ValueError(f"Run already {d['decision']} by {d['approver']} at {d['decidedAt']}; decisions cannot be changed")
        if summ["kind"] == "stress":
            raise ValueError("A stress run is a scenario, not a bid: decide on the base run")
        if decision == "approved" and summ.get("systemBid") is None:
            raise ValueError("The system recommends walking away: there is no bid to approve")
        entry = {"runId": run_id, "decision": decision, "approver": approver, "note": note,
                 "decidedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "bid": summ.get("systemBid"), "maxBid": summ.get("maxBid"), "seed": summ["seed"],
                 "inputHash": summ["inputHash"], "engineVersion": rec["result"].get("version"),
                 "rivalModel": summ.get("rivalModel", "")}
        with self.lock:
            if self._decisions().get(run_id):
                raise ValueError("Run already decided; decisions cannot be changed")
            with self.decisions.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        return entry

    def count(self) -> int:
        if not self.ledger.exists():
            return 0
        with self.ledger.open(encoding="utf-8") as f:
            return sum(1 for _ in f)

    def csv(self) -> str:
        rows = list(reversed(self.list(limit=10_000)))
        buf = io.StringIO()
        cols = ["runId", "kind", "createdAt", "seed", "runs", "inputHash", "durationMs", "systemBid", "pWin",
                "maxBid", "adaptive", "warnings", "reliability", "replayOf", "changes", "rivalModel",
                "decision", "approver", "decidedAt", "note"]
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            d = r.pop("decision", None) or {}
            r.update({"decision": d.get("decision", ""), "approver": d.get("approver", ""),
                      "decidedAt": d.get("decidedAt", ""), "note": d.get("note", "")})
            w.writerow(r)
        return buf.getvalue()
