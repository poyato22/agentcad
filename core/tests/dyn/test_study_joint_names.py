"""A walk study names its joints: a per-joint table (actuator, peak, use of stall) labelled by the
part each joint moves, and the torque chart's legend with the same names, never bare joint ids.
Older records get the table from their saved dyn_torque result; a joint the build has since
re-made is named from the build's history."""
import json
from pathlib import Path

from mises.sim import system_study as ss
from mises.store import Store
from mises.types import StepKind

from . import quad

ROWS = [
    {"joint_id": "fl_knee", "peak_nm": 2.07, "use_of_stall": 0.853, "actuator": "servo_a@1.24",
     "status": "fail", "reason": "peak 85% of stall"},
    {"joint_id": "fl_hip", "peak_nm": 0.5, "use_of_stall": 0.2, "actuator": "servo_a",
     "status": "pass", "reason": "ok"},
]


def test_labels_name_the_part_each_joint_moves():
    asm = quad.assembly()
    labels = ss.joint_labels(asm, ["fl_knee", "fl_hip", "gone"])
    assert labels == {"fl_knee": "fl shank", "fl_hip": "fl thigh"}
    # a joint the build re-made: named from the history's instance
    labels = ss.joint_labels(asm, ["old"], history=lambda ids: {"old": "fl_shank"})
    assert labels == {"old": "fl shank"}


def test_joint_table_and_chart_legend():
    labels = {"fl_knee": "fl shank"}
    table = ss.joint_table(ROWS, labels)
    assert table[0] == {"joint_id": "fl_knee", "label": "fl shank", "actuator": "servo_a@1.24",
                        "peak_nm": 2.07, "use_of_stall": 0.853, "status": "fail"}
    assert table[1]["label"] is None
    charts = [{"id": "c", "series": [{"name": "fl_knee", "points": []},
                                     {"name": "margin", "points": []}]}]
    out = ss.name_series(charts, labels)
    assert [s["name"] for s in out[0]["series"]] == ["fl shank", "margin"]
    assert out[0]["series"][0]["joint_id"] == "fl_knee"


def test_old_record_is_hydrated_from_its_saved_result(tmp_path):
    rid = "dyn_abcdef123456"
    d = tmp_path / "runs" / "run1" / "dyn" / "quad"
    d.mkdir(parents=True)
    (d / f"{rid}.json").write_text(json.dumps({"id": rid, "card": {"rows": ROWS}}))
    rec = {"kind": "walk", "project_id": "quad", "run_id": "run1",
           "sources": [{"tool": "dyn_torque", "result_id": rid}],
           "charts": [{"id": "c", "series": [{"name": "fl_knee", "points": []}]}]}
    out = ss.hydrate_joints(rec, tmp_path, quad.assembly())
    assert [r["label"] for r in out["joints"]] == ["fl shank", "fl thigh"]
    assert out["charts"][0]["series"][0]["name"] == "fl shank"
    # nothing saved: the record is returned as it was
    bare = {"kind": "walk", "project_id": "quad", "run_id": "nope", "sources": [], "charts": []}
    assert "joints" not in ss.hydrate_joints(bare, tmp_path, quad.assembly())


def test_history_finds_the_instance_a_removed_joint_moved(tmp_path: Path):
    st = Store(tmp_path / "m.db")
    run = st.create_run("engineer", "p", "m", "t")
    st.append_step(run.id, StepKind.DIFF, {"name": "assembly_add_joint", "output": {"data": {
        "joint": {"id": "f4f7ee91fa27", "a": {"instance_id": "thigh1"},
                  "b": {"instance_id": "shank1"}}}, "summary": "added revolute joint f4f7ee91fa27"}})
    assert st.joint_refs_from_history(["f4f7ee91fa27", "000000000000", "bad'id"]) == {
        "f4f7ee91fa27": "shank1"}
