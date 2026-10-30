"""Nonlinear static studies (S1): settings, deck pieces and result parsing (pure), plus fast,
coarse end-to-end runs through sim_run with the real CalculiX. The fine benchmarks against the
analytic solutions are in test_nonlinear_bench.py (marker fem_slow, excluded by default)."""
import math
from types import SimpleNamespace

import numpy as np
import pytest

from mises.cad.tools import CreateArgs, mirror_part_create
from mises.sim import nonlinear as nlm
from mises.sim.ccx import find_ccx
from mises.sim.fields import Fields
from mises.sim.tools import SimRunArgs, sim_run
from mises.store import AgentPolicy
from mises.types import Caps, SafetyClass
from mises.sim.deck import Setup, model_cards
from mises.sim.materials import MATERIALS, TANGENT_ESTIMATE_RATIO, custom_material
from mises.sim.mesh import Mesh, Surface, is_face_spec, select_faces
from mises.sim.settings import NonlinearSettings, StaticSettings
from mises.sim.studies import card, check_requirements, fidelity_note, validate_settings
from mises.sim.types import SimError

CASE = {"name": "tip", "supports": [{"kind": "fixed", "face": "x-min"}],
        "loads": [{"kind": "force", "face": "x-max", "newtons": 10, "direction": "-z"}]}


# ---- settings

def test_nonlinear_settings_validate_and_default_to_linear():
    s = StaticSettings.model_validate({"load_cases": [CASE]})
    assert s.nonlinear is None
    s = StaticSettings.model_validate({"load_cases": [CASE], "nonlinear": {
        "large_deflection": True, "plasticity": True, "increments": 20,
        "contacts": [{"a": {"face": "f3"}, "b": {"face": "f5"}, "friction": 0.3}],
        "rigid_supports": [{"face": "bottom", "normal": "+z"}]}})
    nl = s.nonlinear
    assert nl.increments == 20 and nl.contacts[0].kind == "contact"
    assert nl.contacts[0].friction == 0.3 and nl.rigid_supports[0].gap_mm == 0
    with pytest.raises(Exception, match="large_deflection"):
        NonlinearSettings.model_validate({})          # nothing nonlinear asked for
    with pytest.raises(Exception, match="two different"):
        NonlinearSettings.model_validate({"contacts": [{"a": {"face": "f1"},
                                                        "b": {"face": "f1"}}]})
    with pytest.raises(Exception, match="face or an instance"):
        NonlinearSettings.model_validate({"contacts": [{"a": {}, "b": {"face": "f1"}}]})
    with pytest.raises(Exception):
        NonlinearSettings.model_validate({"plasticity": True, "increments": 0})


def test_schema_carries_the_nonlinear_option_for_the_studies_page():
    from mises.sim.studies import types_list
    static = next(t for t in types_list() if t["id"] == "static")
    sch = static["schema"]
    assert "nonlinear" in sch["properties"]
    defs = sch["$defs"]
    nl = defs["NonlinearSettings"]["properties"]
    assert {"large_deflection", "plasticity", "contacts", "rigid_supports", "increments",
            "max_increments", "tangent_modulus_mpa"} <= set(nl)
    assert defs["ContactPair"]["properties"]["friction"]["maximum"] == 2
    assert static["nonlinear_channels"] == ["plastic_strain", "contact_pressure"]
    assert "project_id" in static["targets"]
    assert "refine_points" in defs["MeshSettings"]["properties"]


def test_instance_faces_are_face_specs_and_match_only_their_instance():
    assert is_face_spec("f3@a1b2c3d4e5f6") and not is_face_spec("f3@")
    two = [Surface(tag=1, center=[0, 0, 5], bbox=[-1, -1, 5, 1, 1, 5], area=4, tris=[],
                   instance="aa"),
           Surface(tag=2, center=[0, 0, 5], bbox=[-1, -1, 5, 1, 1, 5], area=4, tris=[],
                   instance="bb")]
    faces = [{"id": "f0@aa", "center": [0, 0, 5]}, {"id": "f1@bb", "center": [0, 0, 5]}]
    bbox = {"min": [-1, -1, 0], "max": [1, 1, 5]}
    assert select_faces("f0@aa", two, bbox, faces) == [1]
    assert select_faces("f1@bb", two, bbox, faces) == [2]   # same centre, other instance


# ---- materials and deck cards

def test_plastic_card_is_bilinear_from_allowable_and_tangent():
    m = MATERIALS["PLA"]
    assert m.tangent_modulus_mpa is None
    assert m.tangent_eff_mpa == pytest.approx(TANGENT_ESTIMATE_RATIO * m.e_mpa)
    assert "estimate" in m.tangent_source and m.to_dict()["tangent_eff_mpa"] > 0
    card_ = nlm.plastic_cards(m, 230.0)
    assert card_[0] == "*PLASTIC"
    s0, e0 = map(float, card_[1].split(","))
    s1, e1 = map(float, card_[2].split(","))
    assert (s0, e0) == (pytest.approx(m.allowable_mpa), 0.0) and e1 == 1.0
    H = m.e_mpa * 230 / (m.e_mpa - 230)
    assert s1 - s0 == pytest.approx(H)
    deck = model_cards(Mesh(nodes={1: (0, 0, 0)}, tets=[], surfaces=[]), m, card_)
    assert deck.index("*PLASTIC") == deck.index("*ELASTIC") + 2
    c = custom_material({"name": "Tough", "e_mpa": 2000, "nu": 0.3, "yield_mpa": 40,
                         "density_g_cm3": 1.2, "tangent_modulus_mpa": 100})
    assert c.tangent_eff_mpa == 100 and c.tangent_source == "material data"


def _tet_mesh() -> Mesh:
    """One linear tet whose base (z = 0) is a surface."""
    nodes = {1: (0.0, 0.0, 0.0), 2: (10.0, 0.0, 0.0), 3: (0.0, 10.0, 0.0), 4: (0.0, 0.0, 10.0)}
    base = Surface(tag=7, center=[3.3, 3.3, 0], bbox=[0, 0, 0, 10, 10, 0], area=50,
                   tris=[[1, 2, 3]])
    return Mesh(nodes=nodes, tets=[[1, 2, 3, 4]], surfaces=[base], size_mm=5.0, order=1)


def test_rigid_support_slab_sits_under_the_face_with_a_contact_pair():
    mesh = _tet_mesh()
    nl = NonlinearSettings.model_validate({"rigid_supports": [{"face": "bottom",
                                                               "normal": "+z",
                                                               "friction": 0.2}]})
    metrics = {"bbox": {"min": [0, 0, 0], "max": [10, 10, 10], "size": [10, 10, 10]}}
    setup = Setup()
    model = nlm.resolve_nonlinear(mesh, metrics, nl, MATERIALS["PLA"], setup)
    assert model.contact and setup.boundary == ["NRIGID1,1,3"]
    assert min(model.rigid_nodes) == 5          # numbered after the part's nodes
    top = max(z for _, _, z in model.rigid_nodes.values())
    assert 0 < top < 1e-2                        # just into the face: contact from the start
    assert "*ELEMENT, TYPE=C3D8, ELSET=ERIGID1" in model.cards
    k = model.cards.index("*CONTACT PAIR, INTERACTION=SIR1, TYPE=SURFACE TO SURFACE")
    assert model.cards[k + 1] == "SP1,SR1"
    assert "*FRICTION" in model.cards
    i = model.cards.index("*SURFACE INTERACTION, NAME=SIR1")
    assert float(model.cards[i + 2]) == pytest.approx(50 * MATERIALS["PLA"].e_mpa)
    # every slab element is a right-handed hexahedron (positive volume), top face S2 up
    nodes = model.rigid_nodes
    el = next(ln for ln in model.cards if ln.startswith("2,"))
    ids = [int(x) for x in el.split(",")[1:]]
    P = np.array([nodes[i] for i in ids])
    jac = np.linalg.det(np.array([P[1] - P[0], P[3] - P[0], P[4] - P[0]]))
    assert jac > 0
    assert all(P[j][2] > P[j - 4][2] for j in range(4, 8))
    step = nlm.nonlinear_step(setup, nl, model)
    assert step[0] == "*STEP, INC=200" and "*CONTACT FILE" in step and "S" in step


def test_step_card_increments_and_nlgeom():
    nl = NonlinearSettings.model_validate({"large_deflection": True, "plasticity": True,
                                           "increments": 20, "max_increments": 50})
    step = nlm.nonlinear_step(Setup(boundary=["N,1,3"]), nl, nlm.NlModel())
    assert step[:3] == ["*STEP, NLGEOM, INC=50", "*STATIC", "0.05,1.0,1e-05,0.05"]
    assert "S,PEEQ" in step and "*CONTACT FILE" not in step
    assert any(ln.startswith("*NODE FILE, FREQUENCY=2") for ln in step)


def test_bonded_pair_is_a_tie_and_unknown_instances_are_refused():
    mesh = _tet_mesh()
    mesh.surfaces.append(Surface(tag=8, center=[0, 3.3, 3.3], bbox=[0, 0, 0, 0, 10, 10],
                                 area=50, tris=[[1, 3, 4]]))
    metrics = {"bbox": {"min": [0, 0, 0], "max": [10, 10, 10], "size": [10, 10, 10]}}
    nl = NonlinearSettings.model_validate({"contacts": [{"kind": "bonded",
                                                         "a": {"face": "bottom"},
                                                         "b": {"face": "x-min"}}]})
    model = nlm.resolve_nonlinear(mesh, metrics, nl, MATERIALS["PLA"], Setup())
    assert "*TIE, NAME=T1, POSITION TOLERANCE=0.1" in model.cards and not model.contact
    nl = NonlinearSettings.model_validate({"contacts": [{"a": {"instance": "zz"},
                                                         "b": {"instance": "yy"}}]})
    with pytest.raises(SimError, match="no instance zz"):
        nlm.resolve_nonlinear(mesh, metrics, nl, MATERIALS["PLA"], Setup())


def test_touching_finds_the_faces_two_instances_share():
    # two unit-ish blocks stacked at z = 10: a's top and b's bottom coincide
    nodes, tris_a, tris_b = {}, [], []
    k = 1
    grid = {}
    for inst, z in (("a", 10.0), ("b", 10.0)):
        for i in range(3):
            for j in range(3):
                nodes[k] = (5.0 * i, 5.0 * j, z + (0.0 if inst == "a" else 1e-4))
                grid[(inst, i, j)] = k
                k += 1
    for inst, tris in (("a", tris_a), ("b", tris_b)):
        for i in range(2):
            for j in range(2):
                g = lambda a, b: grid[(inst, a, b)]  # noqa: E731
                tris += [[g(i, j), g(i + 1, j), g(i + 1, j + 1)],
                         [g(i, j), g(i + 1, j + 1), g(i, j + 1)]]
    far = {k + 1: (0.0, 0.0, 0.0), k + 2: (10.0, 0.0, 0.0), k + 3: (0.0, 10.0, 0.0)}
    nodes.update(far)
    surfs = [Surface(tag=1, center=[5, 5, 10], bbox=[0, 0, 10, 10, 10, 10], area=100,
                     tris=tris_a, instance="a"),
             Surface(tag=2, center=[5, 5, 10], bbox=[0, 0, 10, 10, 10, 10.0001], area=100,
                     tris=tris_b, instance="b"),
             Surface(tag=3, center=[3, 3, 0], bbox=[0, 0, 0, 10, 10, 0], area=50,
                     tris=[[k + 1, k + 2, k + 3]], instance="a")]
    mesh = Mesh(nodes=nodes, tets=[], surfaces=surfs, size_mm=5.0, order=1)
    assert nlm.touching(mesh, "a", "b", 0.1) == [1]
    assert nlm.touching(mesh, "b", "a", 0.1) == [2]
    with pytest.raises(SimError):
        nlm.touching(mesh, "a", "a", 0.1)


# ---- result parsing

STA = """SUMMARY OF JOB INFORMATION
  STEP      INC     ATT  ITRS     TOT TIME     STEP TIME           INC TIME
     1         1     1     4  0.100000E+00  0.100000E+00  0.100000E+00
     1         2     1U    6  0.100000E+00  0.100000E+00  0.100000E+00
     1         2     2     5  0.125000E+00  0.125000E+00  0.250000E-01
     1         3     1U    4  0.125000E+00  0.125000E+00  0.250000E-01
"""


def test_sta_gives_the_load_reached_and_cutbacks(tmp_path):
    p = tmp_path / "job.sta"
    p.write_text(STA)
    rows = nlm.parse_sta(p)
    assert len(rows) == 4 and [r["converged"] for r in rows] == [True, False, True, False]
    assert nlm.load_reached(rows) == pytest.approx(0.125)
    assert nlm.parse_sta(tmp_path / "missing.sta") == []
    assert nlm.load_reached([]) == 0.0


def _frd_block(t: float, name: str, rows: dict) -> str:
    out = [f"  100CL  101{t:12.5E}           3                     0    1           1",
           f" -4  {name:<8}    3    1"]
    for n, vals in rows.items():
        out.append(" -1" + f"{n:10d}" + "".join(f"{v:12.5E}" for v in vals))
    out.append(" -3")
    return "\n".join(out)


def test_frd_increments_drop_blocks_past_the_last_converged_one(tmp_path):
    p = tmp_path / "job.frd"
    p.write_text("\n".join([
        "    1C",
        _frd_block(0.5, "DISP", {1: [0, 0, -1.0]}),
        _frd_block(0.5, "PE", {1: [0.01]}),
        _frd_block(0.75, "DISP", {1: [0, 0, -2.0]}),
        _frd_block(0.75, "CONTACT", {1: [0, 0, 0, 12.5, 0, 0]}),
        _frd_block(0.76, "DISP", {1: [0, 0, -1e9]}),          # the diverged attempt
        " 9999", ""]))
    t, blocks, path = nlm.parse_frd_increments(p, 0.75)
    assert t == pytest.approx(0.75)
    assert blocks["DISP"][1] == pytest.approx([0, 0, -2.0]) and "PE" not in blocks
    assert blocks["CONTACT"][1][3] == pytest.approx(12.5)
    assert path == [(0.5, pytest.approx(1.0)), (0.75, pytest.approx(2.0))]


# ---- cards and checks

def _rec(**r):
    base = {"sim_id": "sim_000000000000", "type": "static", "status": "done",
            "fidelity_class": "fem_nonlinear", "warnings": [],
            "results": {"max_von_mises_mpa": 30, "safety_factor": 1.5, "max_displacement_mm": 2,
                        "error_band_pct": 3, "error_band": 0.03, "model_error_band": 0.15,
                        "load_fraction": 1.0, "max_contact_pressure_mpa": 40,
                        "max_plastic_strain": None,
                        "nonlinear": {"description": "contact"}, **r}}
    return base


def test_card_shows_validated_band_and_nonlinear_metrics():
    rec = _rec()
    assert fidelity_note(rec) == "nonlinear (contact) · ±15% (validated)"
    keys = [m["key"] for m in card(rec)["metrics"]]
    assert "load_fraction" in keys and "max_contact_pressure_mpa" in keys
    assert "max_plastic_strain" not in keys        # not a plastic study
    # a pass inside the validated model error is only a warn
    ch = check_requirements(rec, [{"id": "r1", "kind": "min_safety_factor", "target": 1.2}])
    assert ch[0]["status"] == "warn"


# ---- fast, coarse runs through sim_run (real CalculiX)

def elastica(alpha: float, n: int = 2000) -> tuple[float, float]:
    """Tip (deflection, shortening) / L of a cantilever under a dead transverse tip load with
    alpha = P L^2 / EI: theta'' = -alpha cos(theta), theta(0) = 0, theta'(1) = 0 (shooting on
    theta'(0), RK4)."""
    def shoot(k0: float):
        h = 1.0 / n
        th, k, x, y = 0.0, k0, 0.0, 0.0

        def f(th_, k_):
            return k_, -alpha * math.cos(th_)
        for _ in range(n):
            x += h * math.cos(th + 0.5 * h * k)   # midpoint for the position integrals
            y += h * math.sin(th + 0.5 * h * k)
            a1, b1 = f(th, k)
            a2, b2 = f(th + 0.5 * h * a1, k + 0.5 * h * b1)
            a3, b3 = f(th + 0.5 * h * a2, k + 0.5 * h * b2)
            a4, b4 = f(th + h * a3, k + h * b3)
            th += h / 6 * (a1 + 2 * a2 + 2 * a3 + a4)
            k += h / 6 * (b1 + 2 * b2 + 2 * b3 + b4)
        return k, x, y
    lo, hi = 0.0, alpha
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if shoot(mid)[0] < 0:
            lo = mid
        else:
            hi = mid
    _, x, y = shoot(0.5 * (lo + hi))
    return y, 1.0 - x


def test_elastica_reference_matches_the_published_values():
    # Bisshopp & Drucker (1945): PL^2/EI = 1 -> 0.3017 L, 2 -> 0.4935 L
    assert elastica(1.0)[0] == pytest.approx(0.3017, abs=2e-4)
    assert elastica(2.0)[0] == pytest.approx(0.4935, abs=2e-4)
    assert elastica(1e-3)[0] == pytest.approx(1e-3 / 3, rel=1e-3)   # linear limit


needs_ccx = pytest.mark.skipif(find_ccx() is None, reason="CalculiX (ccx) not installed")
BEAM = ('from build123d import *\nVARS={"length":100.0}\ndef build(v):\n'
        '    return Box(v["length"],10,10,align=(Align.MIN,Align.MIN,Align.MIN))\n')
PLA = MATERIALS["PLA"]


def _ctx(tmp_path, services=None):
    pol = AgentPolicy(run_id="r1", version=1, edit_scope=["*"], read_scope=["*"], caps=Caps(),
                      allowed_classes=list(SafetyClass), created_at=0.0)

    class _Events:
        def publish(self, ev):
            pass
    return SimpleNamespace(workdir=tmp_path, run_id="r1", events=_Events(),
                           services=services or {}, policy=pol, call_id="c1")


async def _part(ctx, pid, script):
    r = await mirror_part_create.handler(ctx, CreateArgs(part_id=pid, script=script))
    assert r.ok, r.summary


async def _run(ctx, pid, settings, **kw):
    return await sim_run.handler(ctx, SimRunArgs(type="static", part_id=pid, settings=settings,
                                                 **kw))


def _fields(tmp_path, pid, out):
    return Fields(tmp_path / "parts" / pid / "sims" / out.data["sim_id"] / "fields.npz")


def tip_load(alpha: float, L=100.0, b=10.0) -> float:
    return alpha * PLA.e_mpa * b ** 4 / 12 / L ** 2


@needs_ccx
@pytest.mark.slow
@pytest.mark.timeout(240)
async def test_fast_large_deflection_cantilever_follows_the_elastica(tmp_path):
    ctx = _ctx(tmp_path)
    await _part(ctx, "beam", BEAM)
    out = await _run(ctx, "beam", {
        "load_cases": [{**CASE, "loads": [{**CASE["loads"][0], "newtons": tip_load(2.0)}]}],
        "material": {"name": "PLA"}, "mesh": {"size_mm": 5},
        "nonlinear": {"large_deflection": True, "increments": 8}})
    assert out.ok, out.summary
    r = out.data["results"]
    assert out.data["fidelity_class"] == "fem_nonlinear"
    assert r["converged"] is True and r["load_fraction"] == pytest.approx(1.0)
    assert r["model_error_band"] == nlm.MODEL_ERROR_BAND["large_deflection"]
    assert "±5% (validated)" in out.data["card"]["fidelity_note"]
    u = _fields(tmp_path, "beam", out).probe([[100.0, 5.0, 5.0]])[0]["values"]["displacement"]
    dv, dh = elastica(2.0)
    assert -u[2] / 100 == pytest.approx(dv, rel=0.05), u        # linear theory: 0.667
    assert -u[0] / 100 == pytest.approx(dh, rel=0.15), u        # the beam shortens
    assert len(r["load_path"]) >= 4 and r["load_path"][-1][0] == pytest.approx(1.0)
    assert "NONLINEAR (large deflection" in out.summary
    assert r["nonlinear"]["validation"][0]["effect"] == "large_deflection"


@needs_ccx
@pytest.mark.slow
@pytest.mark.timeout(240)
async def test_fast_plastic_cantilever_stops_near_the_hinge_load(tmp_path):
    ctx = _ctx(tmp_path)
    await _part(ctx, "beam", BEAM)
    sy = 40.0
    hinge = sy * 10 * 10 ** 2 / 4 / 100                 # Mp / L = 100 N
    mat = {"custom": {"name": "Flow", "e_mpa": 2300, "nu": 0.35, "yield_mpa": sy / 0.7,
                      "density_g_cm3": 1.24, "tangent_modulus_mpa": 0.5}}
    out = await _run(ctx, "beam", {
        "load_cases": [{**CASE, "loads": [{**CASE["loads"][0], "newtons": 1.5 * hinge}]}],
        "material": mat, "mesh": {"size_mm": 5},
        "nonlinear": {"plasticity": True, "increments": 10, "min_increment": 1e-4}})
    assert out.ok, out.summary
    r = out.data["results"]
    assert r["converged"] is False and r["max_plastic_strain"] > 0
    collapse = r["load_fraction"] * 1.5 * hinge
    # a full clamp and a coarse quadratic-tet mesh carry somewhat more than beam theory
    assert hinge * 0.95 < collapse < hinge * 1.3, collapse
    assert r["safety_factor"] <= r["load_fraction"] < 1
    assert out.data["card"]["verdict"] == "fail"
    assert any("stopped at" in w for w in out.data["warnings"])
    f = _fields(tmp_path, "beam", out)
    assert "plastic_strain" in f.channels() and f.units["plastic_strain"] == ""
    near_root = f.probe([[1.0, 5.0, 9.5]])[0]["values"]["plastic_strain"]
    tip = f.probe([[99.0, 5.0, 9.5]])[0]["values"]["plastic_strain"]
    assert near_root > 0 and tip == pytest.approx(0, abs=1e-6)
