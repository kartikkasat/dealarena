"""API checks. Run from the project folder:  python3 -m pytest backend/tests"""
import pytest
from fastapi.testclient import TestClient

from backend import app as appmod


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    # Write logs to a temporary folder during tests.
    d = tmp_path_factory.mktemp("logs")
    appmod.ledger = appmod.RunLedger(d)
    with TestClient(appmod.app) as c:
        yield c


def scenario(client, **sim):
    s = client.get("/api/scenario/default").json()
    s["sim"].update(sim)
    return s


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_simulate_returns_system_bid_and_logs_run(client):
    r = client.post("/api/simulate", json=scenario(client))
    assert r.status_code == 200
    body = r.json()
    assert body["result"]["bidPlan"]["bid"] > 0
    run_id = body["run"]["runId"]
    runs = client.get("/api/runs").json()
    assert runs[0]["runId"] == run_id and runs[0]["seed"] == body["result"]["seed"]
    full = client.get(f"/api/runs/{run_id}").json()
    assert full["inputs"]["deal"]["standaloneValue"] == 1000


def test_fresh_seed_per_run(client):
    s = scenario(client)
    seeds = {client.post("/api/simulate", json=s).json()["result"]["seed"] for _ in range(3)}
    assert len(seeds) == 3


def test_fixed_seed_reproduces_deck(client):
    r = client.post("/api/simulate", json=scenario(client, randomSeed=False)).json()["result"]
    assert round(r["recommendation"]["bid"]) == 1000 and round(r["thresholds"]["ceiling"]) == 1120


def test_replay_is_identical(client):
    run_id = client.post("/api/simulate", json=scenario(client)).json()["run"]["runId"]
    r = client.post(f"/api/runs/{run_id}/replay").json()
    assert r["identical"] is True and r["inputHashMatches"] is True and r["run"]["replayOf"] == run_id


def test_stress(client):
    r = client.post("/api/simulate/stress", json=scenario(client, randomSeed=False))
    assert r.status_code == 200 and r.json()["run"]["kind"] == "stress"


def test_validation_errors_are_readable(client):
    s = scenario(client); s["sim"]["runs"] = 10
    r = client.post("/api/simulate", json=s)
    assert r.status_code == 422 and any("runs" in m for m in r.json()["detail"])


def test_unknown_run(client):
    assert client.get("/api/runs/nope").status_code == 404


def test_csv_export(client):
    r = client.get("/api/runs.csv")
    assert r.status_code == 200 and r.text.startswith("runId,kind,createdAt,seed")


def test_ui_served_but_not_code_or_logs(client):
    assert client.get("/").status_code == 200
    assert client.get("/js/app.js").status_code == 200
    assert client.get("/backend/app.py").status_code == 404
    assert client.get("/logs/runs.jsonl").status_code == 404


def test_model_card_states_what_is_built(client):
    m = client.get("/api/model").json()
    assert m["isTrainedMarl"] is False and m["clientDataUsedForTraining"] is False and m["liveLearning"] is False
    assert [p["stage"] for p in m["pipeline"]] == ["Data", "Calibration", "Training", "Validation", "Serving"]
    r = client.post("/api/simulate", json=scenario(client)).json()
    assert r["run"]["rivalModel"] == m["id"] == r["result"]["rivalModel"]


def test_committee_decision_is_logged_once(client):
    run_id = client.post("/api/simulate", json=scenario(client)).json()["run"]["runId"]
    d = client.post(f"/api/runs/{run_id}/decision", json={"approver": "A. Chair", "decision": "approved", "note": "Within limits"})
    assert d.status_code == 200 and d.json()["bid"] > 0 and d.json()["seed"]
    again = client.post(f"/api/runs/{run_id}/decision", json={"approver": "B. Other", "decision": "rejected"})
    assert again.status_code == 409
    row = next(x for x in client.get("/api/runs").json() if x["runId"] == run_id)
    assert row["decision"]["approver"] == "A. Chair"
    assert "A. Chair" in client.get("/api/runs.csv").text


def test_decision_rules(client):
    stress_id = client.post("/api/simulate/stress", json=scenario(client)).json()["run"]["runId"]
    assert client.post(f"/api/runs/{stress_id}/decision", json={"approver": "A. Chair", "decision": "approved"}).status_code == 409
    assert client.post("/api/runs/nope/decision", json={"approver": "A. Chair", "decision": "approved"}).status_code == 404
    run_id = client.post("/api/simulate", json=scenario(client)).json()["run"]["runId"]
    assert client.post(f"/api/runs/{run_id}/decision", json={"approver": " ", "decision": "approved"}).status_code == 422
