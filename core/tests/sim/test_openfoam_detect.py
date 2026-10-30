"""S2: OpenFOAM detection rows and the allowlisted image pull. Never pulls or runs anything."""

from mises import system_check as sc
from mises.sim import foam_image
from mises.sim.cfd import cfd_solver_status

REAL_IMAGE_STATUS = foam_image.image_status   # conftest fakes it per test


def _rows(monkeypatch, docker, image):
    monkeypatch.setattr("mises.sim.cfd.find_su2", lambda: None)
    monkeypatch.setattr("mises.sim.cfd.find_container_runtime", lambda: docker)
    monkeypatch.setattr("mises.sim.cfd.image_status", lambda d: image)
    return {r["id"]: r for r in cfd_solver_status()}


def test_pinned_image_is_a_digest_of_the_official_arm64_capable_repo():
    assert foam_image.OPENFOAM_IMAGE.startswith("opencfd/openfoam-run@sha256:")
    assert len(foam_image.OPENFOAM_DIGEST) == len("sha256:") + 64
    assert "arm64" in foam_image.OPENFOAM_ARCHES


def test_no_runtime_both_rows_optional_and_no_install(monkeypatch):
    rows = _rows(monkeypatch, None, "absent")
    assert rows["openfoam_container"]["status"] == "optional_missing"
    assert rows["openfoam_container"]["fix"]["kind"] == "open_url"
    assert rows["openfoam"]["status"] == "optional_missing"
    assert rows["openfoam"]["fix"] is None and "Docker" in rows["openfoam"]["detail"]


def test_runtime_without_image_offers_the_pull(monkeypatch):
    rows = _rows(monkeypatch, "/usr/local/bin/docker", "absent")
    assert rows["openfoam_container"]["status"] == "ok"
    r = rows["openfoam"]
    assert r["status"] == "optional_missing"
    assert r["fix"] == {"kind": "install", "label": sc.INSTALLERS["openfoam"].label,
                        "dep": "openfoam"}


def test_daemon_down_says_so_without_a_pull(monkeypatch):
    r = _rows(monkeypatch, "/usr/local/bin/docker", "daemon_down")["openfoam"]
    assert r["status"] == "optional_missing" and r["fix"] is None
    assert "not running" in r["detail"]


def test_image_present_is_ok(monkeypatch):
    r = _rows(monkeypatch, "/usr/local/bin/docker", "present")["openfoam"]
    assert r["status"] == "ok" and r["version"] == "v2512"
    assert r["path"] == foam_image.OPENFOAM_IMAGE


def test_image_status_reads_inspect_only(monkeypatch):
    seen = []

    class R:
        def __init__(self, rc, err):
            self.returncode, self.stderr, self.stdout = rc, err, ""

    def fake_run(argv, **kw):
        seen.append(argv)
        return R(1, "Error: No such image: x")
    monkeypatch.setattr(foam_image.subprocess, "run", fake_run)
    assert REAL_IMAGE_STATUS("/usr/local/bin/docker") == "absent"
    assert seen[0][1:3] == ["image", "inspect"] and seen[0][-1] == foam_image.OPENFOAM_IMAGE
    monkeypatch.setattr(foam_image.subprocess, "run",
                        lambda a, **k: R(1, "Cannot connect to the Docker daemon"))
    assert REAL_IMAGE_STATUS("/usr/local/bin/docker") == "daemon_down"
    monkeypatch.setattr(foam_image.subprocess, "run", lambda a, **k: R(0, ""))
    assert REAL_IMAGE_STATUS("/usr/local/bin/docker") == "present"


def test_installer_is_a_fixed_pull_of_the_pinned_image():
    spec = sc.INSTALLERS["openfoam"]
    assert spec.needs == "docker"
    assert spec.cmd == ("pull", foam_image.OPENFOAM_IMAGE)

