"""Rocket motors: RASP .eng thrust curves (Plan 11 F6).

RASP format (wRASP / ThrustCurve "RASP"): `;` comment lines, one header line
`<designation> <diameter mm> <length mm> <delays a-b-c | P> <propellant kg> <total kg> <mfr>`,
then `<t s> <thrust N>` pairs ending at zero thrust. Comments after the data are ignored.

Where curves come from (sources.json next to the bundled files):
- Bundled: only simfiles ThrustCurve.org lists as Public Domain (F44W, F52C, F67C, G25W,
  G40W). The curves the ARC usually flies on (AeroTech F39T, F42T, F52T, G64W, Estes F15) carry
  no PD/free license there, so they are not shipped.
- Cached: `<home>/rocketry/motors/*.eng`, written by `rocket_motor` (fetched from ThrustCurve's
  API, or from the OpenRocket motor-database mirror of the same simfiles, or the .eng text the
  agent opened on thrustcurve.org). A cached curve is the user's own copy, never redistributed.

Propellant mass burns in proportion to the impulse delivered (OpenRocket's model), so the
motor's mass and CG move during the burn.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..types import ErrorCode, MisesError

BUNDLED_DIR = Path(__file__).parent / "motors"
CACHE_DIR = ("rocketry", "motors")
MAX_ENG_BYTES = 64 * 1024
MAX_POINTS = 2000


@dataclass(frozen=True)
class Motor:
    designation: str
    diameter_mm: float
    length_mm: float
    delays: tuple[float, ...]      # s; () for a plugged motor ("P")
    prop_kg: float
    total_kg: float
    manufacturer: str
    points: tuple[tuple[float, float], ...]   # (t s, thrust N), starting after t = 0
    source: str = "bundled"
    license: str | None = None
    url: str | None = None
    comments: tuple[str, ...] = field(default=(), compare=False)

    # ---- derived numbers

    @property
    def curve(self) -> list[tuple[float, float]]:
        """The thrust curve with an explicit (0, 0) start."""
        pts = list(self.points)
        return pts if pts and pts[0][0] <= 0 else [(0.0, 0.0), *pts]

    @property
    def burn_time_s(self) -> float:
        return self.curve[-1][0]

    @property
    def total_impulse_ns(self) -> float:
        c = self.curve
        return sum((t1 - t0) * (f0 + f1) / 2 for (t0, f0), (t1, f1) in zip(c, c[1:]))

    @property
    def avg_thrust_n(self) -> float:
        return self.total_impulse_ns / self.burn_time_s if self.burn_time_s > 0 else 0.0

    @property
    def max_thrust_n(self) -> float:
        return max(f for _t, f in self.curve)

    @property
    def impulse_class(self) -> str:
        """NAR class letter: A is 1.26-2.5 Ns, each letter doubles."""
        i = self.total_impulse_ns
        if i <= 2.5:
            return "A" if i > 1.25 else "1/2A"
        return chr(ord("A") + max(0, math.ceil(math.log2(i / 2.5))))

    @property
    def common_name(self) -> str:
        """F39 from F39T / F39-6T (letter + average thrust digits)."""
        m = re.match(r"^([A-O])(\d+)", self.designation.upper())
        return f"{m.group(1)}{m.group(2)}" if m else self.designation

    def thrust(self, t: float) -> float:
        c = self.curve
        if t <= 0 or t >= c[-1][0]:
            return 0.0
        for (t0, f0), (t1, f1) in zip(c, c[1:]):
            if t0 <= t <= t1:
                return f0 + (f1 - f0) * (t - t0) / (t1 - t0) if t1 > t0 else f1
        return 0.0

    def impulse_at(self, t: float) -> float:
        """Impulse delivered by time t (Ns)."""
        c = self.curve
        out = 0.0
        for (t0, f0), (t1, f1) in zip(c, c[1:]):
            if t <= t0:
                break
            te = min(t, t1)
            fe = f0 + (f1 - f0) * (te - t0) / (t1 - t0) if t1 > t0 else f1
            out += (te - t0) * (f0 + fe) / 2
        return out

    def mass_kg(self, t: float) -> float:
        """Motor mass at t: the propellant burns in proportion to the impulse delivered."""
        tot = self.total_impulse_ns
        burned = self.impulse_at(t) / tot if tot > 0 else 1.0
        return self.total_kg - self.prop_kg * min(1.0, max(0.0, burned))

    def summary(self) -> dict:
        return {"designation": self.designation, "common_name": self.common_name,
                "manufacturer": self.manufacturer, "class": self.impulse_class,
                "diameter_mm": self.diameter_mm, "length_mm": self.length_mm,
                "total_impulse_ns": round(self.total_impulse_ns, 2),
                "avg_thrust_n": round(self.avg_thrust_n, 2),
                "max_thrust_n": round(self.max_thrust_n, 2),
                "burn_time_s": round(self.burn_time_s, 3),
                "prop_g": round(self.prop_kg * 1000, 1), "total_g": round(self.total_kg * 1000, 1),
                "delays_s": list(self.delays), "source": self.source, "license": self.license,
                "url": self.url}


# ---- RASP parsing

def _delays(tok: str) -> tuple[float, ...]:
    if tok.upper().startswith("P"):
        return ()
    out = []
    for part in re.split(r"[-,]", tok):
        try:
            out.append(float(part))
        except ValueError:
            continue
    return tuple(out)


def parse_eng(text: str, *, source: str = "bundled", license: str | None = None,
              url: str | None = None) -> Motor:
    """The first motor in a RASP .eng text. Untrusted text: only numbers and a short name are
    kept; anything that does not parse is refused with INVALID_INPUT."""
    if len(text.encode("utf-8", "replace")) > MAX_ENG_BYTES:
        raise _bad("the .eng text is too large (a RASP file is a few KB)")
    comments: list[str] = []
    header: list[str] | None = None
    pts: list[tuple[float, float]] = []
    for raw in text.replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith(";"):
            if header is None:
                comments.append(line.lstrip(";").strip()[:200])
            continue
        line = line.split(";", 1)[0].strip()
        toks = line.split()
        if header is None:
            if len(toks) < 7:
                raise _bad("the RASP header needs 7 fields: name diameter length delays "
                           "propellant-kg total-kg manufacturer")
            header = toks
            continue
        if len(toks) < 2:
            continue
        try:
            t, f = float(toks[0]), float(toks[1])
        except ValueError:
            break                                   # a second motor's header: stop at the first
        if not (math.isfinite(t) and math.isfinite(f)) or t < 0 or f < 0:
            raise _bad(f"bad thrust point {line[:40]!r}")
        if pts and t < pts[-1][0]:
            raise _bad("thrust points must be in time order")
        pts.append((t, f))
        if len(pts) > MAX_POINTS:
            raise _bad("too many thrust points")
        if f == 0 and t > 0:
            break                                   # the curve ends at zero thrust
    if header is None or len(pts) < 2:
        raise _bad("no RASP motor found (a header line and thrust points)")
    try:
        d, length, prop, tot = (float(header[1]), float(header[2]), float(header[4]),
                                float(header[5]))
    except ValueError as e:
        raise _bad(f"bad RASP header: {' '.join(header)[:80]!r}") from e
    if not (0 < d < 1000 and 0 < length < 5000 and 0 <= prop <= tot and 0 < tot < 1000):
        raise _bad(f"implausible RASP header: {' '.join(header)[:80]!r}")
    if pts[-1][1] != 0:
        pts.append((pts[-1][0] + 1e-3, 0.0))
    name = re.sub(r"[^A-Za-z0-9.\-/]", "", header[0])[:40] or "motor"
    mfr = re.sub(r"[^A-Za-z0-9 .\-]", "", " ".join(header[6:]))[:40]
    return Motor(name, d, length, _delays(header[3]), prop, tot, mfr, tuple(pts),
                 source=source, license=license, url=url, comments=tuple(comments[:12]))


def format_eng(m: Motor) -> str:
    """A motor back as RASP text (for the cache)."""
    delays = "-".join(f"{d:g}" for d in m.delays) or "P"
    lines = [f"; {c}" for c in m.comments]
    lines.append(f"{m.designation} {m.diameter_mm:g} {m.length_mm:g} {delays} "
                 f"{m.prop_kg:.5g} {m.total_kg:.5g} {m.manufacturer or 'unknown'}")
    lines += [f"   {t:.4f} {f:.3f}" for t, f in m.points]
    return "\n".join(lines) + "\n"


# ---- the library

def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _strip_mfr(name: str) -> str:
    return re.sub(r"^(aerotech|aero tech|at|estes|cesaroni|cti)[\s_-]+", "", name.strip(),
                  flags=re.I)


def parse_common(name: str) -> str:
    """The common name (letter + average thrust) a designation implies: 'F39-6T' -> 'F39'."""
    m = re.match(r"^([A-O])(\d+)", _strip_mfr(name).strip().upper())
    return f"{m.group(1)}{m.group(2)}" if m else _strip_mfr(name).strip()


def _sources() -> dict:
    try:
        return json.loads((BUNDLED_DIR / "sources.json").read_text())["files"]
    except (OSError, ValueError, KeyError):
        return {}


def bundled() -> list[Motor]:
    src = _sources()
    out = []
    for p in sorted(BUNDLED_DIR.glob("*.eng")):
        meta = src.get(p.name, {})
        out.append(parse_eng(p.read_text(), source="bundled", license=meta.get("license"),
                             url=meta.get("url")))
    return out


def cache_dir(home: Path | None) -> Path | None:
    return Path(home).joinpath(*CACHE_DIR) if home is not None else None


def cached(home: Path | None) -> list[Motor]:
    d = cache_dir(home)
    if d is None or not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.eng")):
        meta: dict = {}
        side = p.with_suffix(".json")
        try:
            if side.is_file():
                meta = json.loads(side.read_text())
            out.append(parse_eng(p.read_text(), source=str(meta.get("source") or "cached"),
                                 license=meta.get("license"), url=meta.get("url")))
        except (OSError, ValueError, MisesError):
            continue
    return out


def library(home: Path | None) -> list[Motor]:
    """Every motor Mises knows: the user's cached curves first (they win a name clash)."""
    return [*cached(home), *bundled()]


def find(name: str, home: Path | None) -> Motor | None:
    """A motor by designation ("F52C", "AeroTech F52", "F52-8C", "f52"): an exact designation
    first, then the common name (letter + thrust), then a designation prefix."""
    want = _norm(_strip_mfr(name))
    want_nodelay = _norm(re.sub(r"-\d+(?=[A-Za-z]*$)", "", _strip_mfr(name)))
    lib = library(home)
    for m in lib:
        if _norm(m.designation) in (want, want_nodelay):
            return m
    for m in lib:
        if _norm(m.common_name) == want_nodelay or _norm(m.common_name) == want:
            return m
    for m in lib:
        if _norm(m.designation).startswith(want_nodelay) and want_nodelay:
            return m
    return None


def save(home: Path, m: Motor, *, source: str, license: str | None = None,
         url: str | None = None) -> Path:
    """Write a curve into the user's cache (<home>/rocketry/motors/<designation>.eng)."""
    d = cache_dir(home)
    assert d is not None
    d.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", f"{m.manufacturer.split(' ')[0] or 'motor'}_"
                                          f"{m.designation}")[:60]
    p = d / f"{stem}.eng"
    p.write_text(format_eng(m))
    p.with_suffix(".json").write_text(json.dumps({"source": source, "license": license,
                                                  "url": url}))
    return p


def _bad(msg: str, hint: str | None = None) -> MisesError:
    return MisesError(ErrorCode.INVALID_INPUT, msg, hint=hint)


def resolve(name: str | None, eng: str | None, home: Path | None) -> Motor:
    """The motor a call names, or the .eng text it carries."""
    if eng:
        return parse_eng(eng, source="given (.eng text)")
    if not name:
        raise _bad("which motor?", hint="pass motor (e.g. 'F52C') or motor_eng (RASP text)")
    m = find(name, home)
    if m is None:
        known = ", ".join(sorted({x.designation for x in library(home)}))
        raise MisesError(
            ErrorCode.NOT_FOUND, f"no thrust curve for {name!r} in Mises",
            hint=f"known: {known}. Fetch it with rocket_motor (action 'fetch'), or open its "
                 "RASP (.eng) file on thrustcurve.org and pass the text as motor_eng.")
    return m
