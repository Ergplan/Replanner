"""The HTTP boundary for projects and scenarios, against a throwaway store."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "worker"))
sys.path.insert(0, str(ROOT / "services" / "api"))
import jobs as J  # noqa: E402
import main  # noqa: E402
import projects as P  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "PROJECTS", tmp_path / "_projects")
    monkeypatch.setattr(J, "JOBS", tmp_path / "_jobs")
    monkeypatch.setattr(J, "RUNS", tmp_path)
    main._fingerprint.cache_clear()
    return TestClient(main.app)


def _load_csv(kw=4000.0, rows=None):
    t = pd.date_range("2026-01-01", "2027-01-01", freq="15min", inclusive="left")
    df = pd.DataFrame({"timestamp": t.strftime("%Y-%m-%d %H:%M"), "load_kw": kw})
    return (df if rows is None else df.iloc[:rows]).to_csv(index=False).encode()


def test_a_project_is_created_edited_and_given_its_own_load(client):
    pid = client.post("/projects", json={"name": "Chakan unit"}).json()["project_id"]
    p = client.get(f"/projects/{pid}").json()
    assert p["problems"] == [] and not p["sample"]

    inp = p["inputs"]
    inp["project"]["site"]["import_limit_mw"] = 20.0
    assert client.put(f"/projects/{pid}/inputs", json=inp).status_code == 200

    r = client.post(f"/projects/{pid}/series/load_mw", files={"file": ("l.csv", _load_csv())},
                    data={"unit": "kW"})
    assert r.status_code == 200 and r.json()["annual_mwh"] == pytest.approx(4.0 * 8760)

    meta = client.get("/project", params={"project_id": pid}).json()
    assert meta["site"]["import_limit_mw"] == 20.0 and meta["provenance"] == "mixed"


def test_a_value_the_schema_rejects_names_its_field(client):
    pid = client.post("/projects", json={"name": "x"}).json()["project_id"]
    inp = client.get(f"/projects/{pid}").json()["inputs"]
    inp["battery"]["technical"]["eta_charge"] = 1.4
    r = client.put(f"/projects/{pid}/inputs", json=inp)
    assert r.status_code == 422
    assert r.json()["detail"]["invalid"][0]["field"] == "battery.technical.eta_charge"


def test_an_unsolvable_input_is_saved_and_reported(client):
    pid = client.post("/projects", json={"name": "x"}).json()["project_id"]
    inp = client.get(f"/projects/{pid}").json()["inputs"]
    inp["project"]["site"]["roof_area_mw_cap"] = 0.5          # less than the 1 MWp already there
    r = client.put(f"/projects/{pid}/inputs", json=inp).json()
    assert any("exceeds the site's roof" in s for s in r["problems"])
    assert client.post("/scenarios", json={"project_id": pid, "mode": "find_optimum"}
                       ).status_code == 400


def test_a_gappy_load_file_is_refused_with_the_gap(client):
    pid = client.post("/projects", json={"name": "x"}).json()["project_id"]
    r = client.post(f"/projects/{pid}/series/load_mw",
                    files={"file": ("l.csv", _load_csv(rows=1000))}, data={"unit": "kW"})
    assert r.status_code == 400
    assert "missing" in r.json()["detail"]["errors"][0]


def test_the_sample_cannot_be_edited(client):
    inp = client.get(f"/projects/{P.SAMPLE_ID}").json()["inputs"]
    assert client.put(f"/projects/{P.SAMPLE_ID}/inputs", json=inp).status_code == 403


def test_find_optimum_twice_is_one_job_and_a_new_scenario_replaces_the_old(client):
    a = client.post("/scenarios", json={"mode": "find_optimum"}).json()
    b = client.post("/scenarios", json={"mode": "find_optimum"}).json()
    assert a["job_id"] == b["job_id"]
    caps = {"solar_onsite_mw": 7.0, "solar_remote_mw": 16.0, "wind_remote_mw": 12.0,
            "bess_power_mw": 10.0, "bess_energy_mwh": 60.0}
    first = client.post("/scenarios", json={"mode": "manual", "capacities": caps}).json()
    second = client.post("/scenarios", json={"mode": "manual", "capacities": caps}).json()
    assert client.get(f"/jobs/{first['job_id']}").json()["status"] == "cancelled"
    assert client.get(f"/jobs/{second['job_id']}").json()["status"] == "queued"


def test_the_current_fingerprint_follows_an_upload(client):
    pid = client.post("/projects", json={"name": "x"}).json()["project_id"]
    before = client.get("/project", params={"project_id": pid}).json()["input_fingerprint"]
    client.post(f"/projects/{pid}/series/load_mw", files={"file": ("l.csv", _load_csv())},
                data={"unit": "kW"})
    after = client.get("/project", params={"project_id": pid}).json()["input_fingerprint"]
    assert before and after and before != after


def test_any_series_can_be_replaced_and_reverted(client):
    pid = client.post("/projects", json={"name": "x"}).json()["project_id"]
    before = client.get("/project", params={"project_id": pid}).json()["input_fingerprint"]
    t = pd.date_range("2026-01-01", "2027-01-01", freq="60min", inclusive="left")
    csv = pd.DataFrame({"time": t.strftime("%Y-%m-%d %H:%M"), "wind_pct": 31.0}).to_csv(
        index=False).encode()
    r = client.post(f"/projects/{pid}/series/wind_remote_cf", files={"file": ("w.csv", csv)},
                    data={"unit": "%"})
    assert r.status_code == 200 and r.json()["annual_cf"] == pytest.approx(0.31)
    rows = {s["column"]: s for s in client.get(f"/projects/{pid}").json()["series"]}
    assert rows["wind_remote_cf"]["source"] == "uploaded" and "%" in rows["wind_remote_cf"]["units"]
    assert rows["solar_remote_cf"]["source"] == "sample"
    assert client.get("/project", params={"project_id": pid}).json()["input_fingerprint"] != before
    client.delete(f"/projects/{pid}/series/wind_remote_cf")
    rows = {s["column"]: s for s in client.get(f"/projects/{pid}").json()["series"]}
    assert rows["wind_remote_cf"]["source"] == "sample"
    assert client.post(f"/projects/{pid}/series/nonsense", files={"file": ("w.csv", csv)}
                       ).status_code == 404


def test_a_run_downloads_as_csv(client, tmp_path):
    rid = "find_optimum-test"
    d = tmp_path / rid
    d.mkdir()
    t = pd.date_range("2026-01-01", periods=4, freq="15min", tz="Asia/Kolkata").tz_convert("UTC")
    pd.DataFrame({"timestamp_utc": t, "load_mw": 2.0, "served_load_mw": 2.0,
                  "renewable_used_mw": 0.5, "import_utility_mw": 1.5, "import_market_mw": 0.0,
                  "bank_in_mw": 0.0, "bank_out_mw": 0.0, "bank_lapse_mwh": 0.0}
                 ).to_parquet(d / "dispatch.parquet")
    (d / "summary.json").write_text('{"run_id": "find_optimum-test", "mode": "find_optimum", '
                                    '"ledger": {"utility_energy": 12000.0, "total_annual_cost": 12000.0},'
                                    ' "capacities": {"bess_energy_mwh": 4.0}}')
    r = client.get(f"/runs/{rid}/export.csv")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    first = r.text.splitlines()[1]
    assert first.startswith("2026-01-01 00:00,2025-12-31T18:30:00Z")        # IST beside UTC
    summary = pd.read_csv(__import__("io").StringIO(client.get(f"/runs/{rid}/summary.csv").text))
    per_kwh = summary[(summary.section == "cost per kWh") & (summary.item == "total_annual_cost")]
    assert float(per_kwh.value.iloc[0]) == pytest.approx(12000.0 / 2000.0)  # 2 MW for an hour
    assert "banked" not in set(summary.item)
