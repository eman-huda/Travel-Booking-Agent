"""Real destination information.

Description: Wikipedia summary. Transport and safety: Wikivoyage text. Attractions: Wikivoyage
structured see/do listings from the city page and its district pages, with OpenStreetMap
(Overpass) as a backup because public Overpass servers are often overloaded.
"""
from __future__ import annotations

import html
import re

from app.providers.base import DestinationProvider
from app.providers.live.geo import LiveGeo
from app.providers.live.http import CachedHTTP
from app.tools.errors import ToolTimeoutError, UpstreamAPIError

WIKIPEDIA_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
WIKIVOYAGE_API = "https://en.wikivoyage.org/w/api.php"
OVERPASS_URLS = ("https://overpass-api.de/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter")
MAX_DISTRICTS = 5
INDOOR_WORDS = ("museum", "gallery", "mall", "aquarium", "palace", "mosque", "cistern", "souk", "souq",
                "market", "bazaar", "cathedral", "church", "temple")
FREE_WORDS = ("free", "free.", "free entry", "free admission", "no charge")

TOURISM = {  # OpenStreetMap tag: (category, duration hours, best time, indoor)
    "museum": ("museum", 2.0, "afternoon", True),
    "gallery": ("gallery", 1.5, "afternoon", True),
    "aquarium": ("family", 2.0, "afternoon", True),
    "zoo": ("family", 2.5, "morning", False),
    "theme_park": ("family", 4.0, "afternoon", False),
    "viewpoint": ("viewpoint", 1.0, "evening", False),
    "attraction": ("landmark", 1.5, "morning", False),
}

LINK_RE = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]")
NOISE_RE = re.compile(r"\{\{[^}]*\}\}|<[^>]+>|'{2,3}")


def _clean(value: str) -> str:
    return " ".join(html.unescape(NOISE_RE.sub("", LINK_RE.sub(r"\1", value))).split())


def _field(body: str, name: str) -> str | None:
    m = re.search(rf"(?:^|\|)\s*{name}\s*=\s*([^|]*)", body)
    if not m:
        return None
    value = _clean(m.group(1))
    return value or None


def parse_listings(wikitext: str, page: str) -> list[dict]:
    """Extracts structured {{see}} and {{do}} listings from Wikivoyage wikitext."""
    out = []
    for kind, body in re.findall(r"\{\{\s*(see|do)\s*\|(.*?)\}\}", wikitext, flags=re.S | re.I):
        body = body.replace("\n", " ")
        name = _field(body, "name")
        if not name:
            continue
        price = _field(body, "price")
        lower = name.lower()
        indoor = any(w in lower for w in INDOOR_WORDS)
        if kind.lower() == "do":
            best, hours, category = "afternoon", 2.0, "activity"
        else:
            best, hours, category = ("afternoon" if indoor else "morning"), (2.0 if indoor else 1.5), "sight"
        is_free = bool(price) and price.strip().lower() in FREE_WORDS
        out.append({"attraction_id": "WV-" + re.sub(r"[^a-z0-9]+", "-", lower).strip("-")[:40],
                    "name": name[:80], "category": category, "estimated_cost": 0.0 if is_free else None,
                    "currency": None, "duration_hours": hours, "best_time": best, "indoor": indoor,
                    "price_note": price[:80] if price and not is_free else None,
                    "source": f"Wikivoyage ({page})"})
    return out


def _section(text: str, heading: str, limit: int = 600) -> str | None:
    m = re.search(rf"==\s*{re.escape(heading)}\s*==\n(.*?)(?=\n==[^=]|\Z)", text, flags=re.S)
    if not m:
        return None
    body = re.sub(r"\n=+[^=\n]+=+\n", "\n", m.group(1)).strip()
    body = " ".join(body.split())
    if len(body) > limit:
        body = body[:limit].rsplit(". ", 1)[0] + "."
    return body or None


class LiveDestinations(DestinationProvider):
    source = "wikipedia+wikivoyage+openstreetmap"

    def __init__(self, http: CachedHTTP, geo: LiveGeo):
        self.http, self.geo = http, geo

    def resolve_city(self, name):
        return self.geo.resolve_city(name)

    def known_cities(self):
        return self.geo.known_cities()

    # ---------------------------------------------------------------- sources
    def _wikivoyage_wikitext(self, title: str) -> str:
        body, _ = self.http.get_json(WIKIVOYAGE_API, service="Wikivoyage", params={
            "action": "parse", "prop": "wikitext", "page": title, "format": "json", "formatversion": "2",
            "redirects": "1"})
        if "error" in body:
            raise UpstreamAPIError(404, f"Wikivoyage has no page '{title}'")
        return body["parse"]["wikitext"]

    def _wikivoyage_attractions(self, city: dict) -> list[dict]:
        main = self._wikivoyage_wikitext(city["name"])
        listings = parse_listings(main, city["name"])
        districts = list(dict.fromkeys(re.findall(rf"\[\[({re.escape(city['name'])}/[^\]|#]+)", main)))[:MAX_DISTRICTS]
        for district in districts:
            try:
                listings += parse_listings(self._wikivoyage_wikitext(district), district)
            except (UpstreamAPIError, ToolTimeoutError):
                continue
        seen, unique = set(), []
        for item in listings:
            if item["name"].lower() not in seen:
                seen.add(item["name"].lower())
                unique.append(item)
        unique.sort(key=lambda a: a["category"] != "sight")  # sights first, otherwise source order
        return unique[:20]

    def _osm_attractions(self, city: dict) -> list[dict]:
        query = (f'[out:json][timeout:25];nwr(around:9000,{city["lat"]},{city["lon"]})'
                 f'["tourism"~"^({"|".join(TOURISM)})$"]["name"];out center 80;')
        body, last_exc = None, None
        for url in OVERPASS_URLS:
            try:
                body, _ = self.http.post_form(url, service="Attractions service (OpenStreetMap)", data={"data": query})
                break
            except (UpstreamAPIError, ToolTimeoutError) as exc:
                last_exc = exc
        if body is None:
            raise last_exc
        elements = sorted(body.get("elements", []),
                          key=lambda e: (not e.get("tags", {}).get("wikipedia"), not e.get("tags", {}).get("wikidata"),
                                         (e.get("tags", {}).get("name:en") or e.get("tags", {}).get("name", "")).lower()))
        seen, out = set(), []
        for e in elements:
            tags = e.get("tags", {})
            name = tags.get("name:en") or tags.get("name")
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            cat, hours, best, indoor = TOURISM.get(tags.get("tourism"), TOURISM["attraction"])
            out.append({"attraction_id": f"OSM-{e['type'][0]}{e['id']}", "name": name, "category": cat,
                        "estimated_cost": None, "currency": None, "duration_hours": hours,
                        "best_time": best, "indoor": indoor, "source": "OpenStreetMap"})
            if len(out) >= 15:
                break
        return out

    # ---------------------------------------------------------------- tool
    def get_destination_info(self, destination):
        city = self.geo.require(destination)
        warnings: list[str] = []
        description = None
        try:
            body, _ = self.http.get_json(WIKIPEDIA_SUMMARY.format(title=city["name"].replace(" ", "_")), service="Wikipedia")
            description = body.get("extract")
        except (UpstreamAPIError, ToolTimeoutError) as exc:
            warnings.append(f"Description unavailable: {exc}")

        transport, general = [], []
        try:
            body, _ = self.http.get_json(WIKIVOYAGE_API, service="Wikivoyage", params={
                "action": "query", "prop": "extracts", "explaintext": "1", "titles": city["name"],
                "format": "json", "redirects": "1"})
            page = next(iter(body.get("query", {}).get("pages", {}).values()), {})
            text = page.get("extract") or ""
            for heading, bucket in (("Get around", transport), ("Get in", transport), ("Stay safe", general)):
                sec = _section(text, heading)
                if sec:
                    bucket.append(f"Wikivoyage, {heading}: {sec}")
        except (UpstreamAPIError, ToolTimeoutError) as exc:
            warnings.append(f"Travel guide text unavailable: {exc}")
        general.insert(0, f"{city['name']} is in {city['country']}. Local currency: {city['local_currency'] or 'unknown'}. "
                          f"Airport(s) searched: {', '.join(city['iata_list'])}.")
        general.append("Check official government sources for current entry and visa requirements before travel.")

        attractions: list[dict] = []
        try:
            attractions = self._wikivoyage_attractions(city)
        except (UpstreamAPIError, ToolTimeoutError) as exc:
            warnings.append(f"Wikivoyage listings unavailable: {exc}")
        if len(attractions) < 6:
            try:
                names = {a["name"].lower() for a in attractions}
                attractions += [a for a in self._osm_attractions(city) if a["name"].lower() not in names]
            except (UpstreamAPIError, ToolTimeoutError) as exc:
                if not attractions:
                    warnings.append(f"Attractions could not be retrieved: {exc}")
        if not attractions and not any("Attractions" in w for w in warnings):
            warnings.append(f"No named attractions were found for {city['name']}.")
        return {"status": "success", "destination": city["name"], "country": city["country"],
                "description": description or f"No description was available for {city['name']}.",
                "local_currency": city["local_currency"] or "USD", "attractions": attractions,
                "transport": transport or ["No transport information was available from Wikivoyage."],
                "general_info": general, "warnings": warnings, "source": self.source}
