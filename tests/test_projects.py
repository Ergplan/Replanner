"""The project store: a user's own inputs and uploads, and the read-only sample."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "worker"))
import projects as P  # noqa: E402

from energy_core.optimization import build_spec  # noqa: E402


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "PROJECTS", tmp_path / "_projects")


def test_a_new_project_starts_from_the_sample_under_its_own_name():
    pid = P.create_project("Pune Plant 2")
    assert pid.startswith("pune-plant-2-")
    inp = P.load_inputs(pid)
    assert inp.project.name == "Pune Plant 2" and inp.project.project_id == pid
    assert [p["project_id"] for p in P.list_projects()][:2] == [P.SAMPLE_ID, pid]


def test_the_sample_is_read_only():
    with pytest.raises(P.SampleIsReadOnly):
        P.save_inputs(P.SAMPLE_ID, P.load_inputs(P.SAMPLE_ID))
    with pytest.raises(P.SampleIsReadOnly):
        P.save_series(P.SAMPLE_ID, 2026, "load_mw", np.ones(3))


def test_edits_persist():
    pid = P.create_project("site")
    inp = P.load_inputs(pid)
    inp.project.site.import_limit_mw = 22.0
    P.save_inputs(pid, inp)
    assert P.load_inputs(pid).project.site.import_limit_mw == 22.0


def test_an_upload_replaces_only_its_own_series():
    pid = P.create_project("site")
    sample = P.sample_frame(2026)
    load = np.full(len(sample), 3.0)
    P.save_series(pid, 2026, "load_mw", load)
    f = P.load_frame(pid, 2026)
    assert f["load_mw"].to_numpy() == pytest.approx(load)
    assert f["solar_remote_cf"].to_numpy() == pytest.approx(sample["solar_remote_cf"].to_numpy())
    status = {s["column"]: s for s in P.series_status(pid, 2026)}
    assert status["load_mw"]["source"] == "uploaded"
    assert status["load_mw"]["annual_mwh"] == pytest.approx(3.0 * 8760)
    assert status["wind_remote_cf"]["source"] == "sample"
    P.delete_series(pid, 2026, "load_mw")
    assert P.load_frame(pid, 2026)["load_mw"].to_numpy() == pytest.approx(
        sample["load_mw"].to_numpy())


def test_a_user_project_does_not_inherit_the_samples_test_outage():
    pid = P.create_project("site")
    assert (P.sample_frame(2026)["grid_available"] < 1).any()
    assert (P.load_frame(pid, 2026)["grid_available"] == 1.0).all()
    status = {s["column"]: s["source"] for s in P.series_status(pid, 2026)}
    assert status["grid_available"].startswith("assumed")


def test_a_project_builds_a_spec_and_its_fingerprint_follows_its_inputs():
    pid = P.create_project("site")
    fp = lambda: build_spec(P.load_inputs(pid), P.load_frame(pid, 2026), 2026).fingerprint()
    before = fp()
    P.save_series(pid, 2026, "load_mw", np.full(len(P.sample_frame(2026)), 3.0))
    assert fp() != before


def test_ids_cannot_escape_the_store():
    with pytest.raises(P.ProjectNotFound):
        P.load_inputs("../../etc")
