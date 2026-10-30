"""The suggested sites: a curated, user-editable list (profile `suggested_sites`) that agents
may read without an approval card. Each entry may carry a search URL template (`{q}` is
replaced by the URL-encoded query) that web_search reads.

The defaults are marked `suggested: true` (the user's own additions are not). Some of them
block automated access (Amazon, AliExpress and McMaster-Carr often do): web_search then says
"site blocked automated access" for that site and the others still answer.
"""
from __future__ import annotations

import re
from urllib.parse import quote, quote_plus

CATEGORIES = ("fasteners", "bearings", "mechanical", "materials", "electronics", "motors",
              "servos", "robotics", "fpv", "maker", "general", "datasheets")

# query word -> categories it points at (web_search picks sites by these when none are named)
KEYWORDS: dict[str, tuple[str, ...]] = {
    "fasteners": ("screw", "screws", "bolt", "bolts", "nut", "nuts", "washer", "washers",
                  "standoff", "standoffs", "insert", "inserts", "rivet", "dowel", "spacer"),
    "bearings": ("bearing", "bearings", "bushing", "bushings", "shaft", "linear", "rail",
                 "pulley", "belt", "gt2", "gear", "gears", "coupling", "coupler", "608", "625"),
    "materials": ("extrusion", "tube", "tubing", "rod", "sheet", "plate", "aluminum",
                  "aluminium", "carbon", "2020", "2040"),
    "electronics": ("resistor", "capacitor", "sensor", "connector", "regulator", "mosfet",
                    "chip", "led", "microcontroller", "board", "driver", "ic", "imu", "esp32",
                    "arduino", "raspberry", "encoder", "pcb", "relay"),
    "motors": ("motor", "motors", "stepper", "nema", "nema17", "brushless", "bldc",
               "gearmotor", "actuator", "esc"),
    "servos": ("servo", "servos", "sg90", "mg996r"),
    "robotics": ("robot", "robotic", "arm", "gripper", "wheel", "wheels", "chassis", "track"),
    "fpv": ("fpv", "drone", "quad", "quadcopter", "prop", "props", "propeller", "lipo",
            "flight", "vtx", "frame"),
}


def _site(name: str, domain: str, search_url: str | None, categories: list[str]) -> dict:
    return {"name": name, "domain": domain, "search_url": search_url,
            "categories": categories, "suggested": True}


DEFAULT_SITES: list[dict] = [
    _site("McMaster-Carr", "mcmaster.com", "https://www.mcmaster.com/{q}/",
          ["fasteners", "bearings", "mechanical", "materials"]),
    _site("Misumi", "misumi-ec.com", "https://us.misumi-ec.com/vona2/result/?Keyword={q}",
          ["fasteners", "bearings", "mechanical", "materials"]),
    _site("Digi-Key", "digikey.com", "https://www.digikey.com/en/products/result?keywords={q}",
          ["electronics", "motors"]),
    _site("Mouser", "mouser.com", "https://www.mouser.com/c/?q={q}", ["electronics"]),
    _site("Adafruit", "adafruit.com", "https://www.adafruit.com/search?q={q}",
          ["electronics", "maker", "servos", "motors"]),
    _site("SparkFun", "sparkfun.com", "https://www.sparkfun.com/catalogsearch/result/?q={q}",
          ["electronics", "maker", "motors"]),
    _site("Pololu", "pololu.com", "https://www.pololu.com/search?query={q}",
          ["motors", "servos", "robotics", "electronics"]),
    _site("ServoCity", "servocity.com", "https://www.servocity.com/search.php?search_query={q}",
          ["servos", "motors", "robotics", "mechanical", "bearings"]),
    _site("GetFPV", "getfpv.com", "https://www.getfpv.com/catalogsearch/result/?q={q}",
          ["fpv", "motors"]),
    _site("RobotShop", "robotshop.com", "https://www.robotshop.com/search?q={q}",
          ["robotics", "servos", "motors"]),
    _site("AliExpress", "aliexpress.com", "https://www.aliexpress.com/wholesale?SearchText={q}",
          ["general"]),
    _site("Amazon", "amazon.com", "https://www.amazon.com/s?k={q}", ["general"]),
    # manufacturer datasheet hosts: read with web_open (no search page Mises can read)
    _site("Texas Instruments", "ti.com", None, ["datasheets", "electronics"]),
    _site("STMicroelectronics", "st.com", None, ["datasheets", "electronics"]),
    _site("Analog Devices", "analog.com", None, ["datasheets", "electronics"]),
    _site("Microchip", "microchip.com", None, ["datasheets", "electronics"]),
    _site("Raspberry Pi", "raspberrypi.com", None, ["datasheets", "electronics"]),
    _site("Arduino", "arduino.cc", None, ["datasheets", "electronics"]),
]


def on_domain(host: str, domain: str) -> bool:
    """`host` is `domain` itself or one of its subdomains."""
    host, domain = host.lower().rstrip("."), domain.lower().rstrip(".")
    return host == domain or host.endswith("." + domain)


def site_for_host(sites: list[dict], host: str) -> dict | None:
    return next((s for s in sites if on_domain(host, s["domain"])), None)


def search_url(site: dict, query: str) -> str:
    """The site's search page for `query`: `{q}` is URL-encoded (`+` for spaces in a query
    string, `%20` in a path)."""
    tpl = site["search_url"]
    in_query = "?" in tpl and tpl.index("?") < tpl.index("{q}")
    q = quote_plus(query) if in_query else quote(query.replace(" ", "-"), safe="-")
    return tpl.replace("{q}", q)


def pick_sites(sites: list[dict], query: str, wanted: list[str] | None = None,
               limit: int = 3) -> list[dict]:
    """The sites web_search reads. Named ones (`wanted`: names or domains; KeyError for one not
    on the list, LookupError for one without a search page) when given; else the
    searchable sites whose categories the query's words point at, best match first, then the
    list's own order; with no match, the first searchable sites in the list."""
    searchable = [s for s in sites if s.get("search_url")]
    if wanted:
        out = []
        for w in wanted:
            w = w.strip().lower()
            hit = next((s for s in sites if s["name"].lower() == w
                        or on_domain(w, s["domain"])), None)
            if hit is None:
                raise KeyError(w)
            if not hit.get("search_url"):
                raise LookupError(hit["name"])
            if hit not in out:
                out.append(hit)
        return out
    words = set(re.findall(r"[a-z0-9]+", query.lower()))
    cats = {c for c, kws in KEYWORDS.items() if words & set(kws)}
    scored = [(len(cats & set(s.get("categories") or [])), i, s)
              for i, s in enumerate(searchable)]
    hits = [t for t in scored if t[0] > 0]
    if not hits:
        return searchable[:limit]
    hits.sort(key=lambda t: (-t[0], t[1]))
    return [t[2] for t in hits[:limit]]
