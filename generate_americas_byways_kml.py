#!/usr/bin/env python3
"""
Generate a KML file for all current FHWA America's Byways.

Styles:
- All-American Roads: #ff00ff, 5 pt
- National Scenic Byways: #00ff00, 2 pt
"""

from __future__ import annotations

import csv
import html
import json
import math
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import generate_all_american_roads_kml as base


OUT_KML = Path("americas_byways.kml")
OUT_REPORT = Path("americas_byways_coverage_report.csv")
OUT_LIST = Path("fhwa_americas_byways_list.csv")
PLACE_CACHE = Path("endpoint_place_cache.json")
CONTINUITY_THRESHOLD_METERS = 1000.0
ARCGIS_LAYER_NAME_FIELD = dict(base.ARCGIS_LAYER_NAME_FIELD)
ARCGIS_LAYER_NAME_FIELD[6] = "Byway_Name"


@dataclass(frozen=True)
class Byway:
    fhwa_id: str
    name: str
    states: str
    designations: str
    is_all_american_road: bool
    is_national_scenic_byway: bool
    source_url: str


@dataclass
class EndpointMarker:
    part_index: int
    marker_type: str
    point: tuple[float, float]
    place_name: str
    full_place: str


EXTRA_ARCGIS_ALIASES = {
    "Acadia All-American Road": ["Acadia Byway", "Acadia National Park Loop Road"],
    "Arroyo Seco Historic Parkway - Route 110": ["Arroyo Seco Historic Parkway"],
    "Avenue of the Saints": ["Avenue of Saints"],
    "Battle Road Scenic Byway": ["Battle Road"],
    "Cherohala Skyway": ["Cherohala Skyway National Scenic Byway"],
    "City of Las Vegas, Las Vegas Boulevard National Scenic Byway": [
        "City of Las Vegas, Las Vegas Boulevard State Scenic Byway",
        "Las Vegas Strip",
    ],
    "Cumberland Historic Byway": ["Cumberland Cultural Heritage Highway"],
    "Delaware Bayshore Byway": ["Bayshore Heritage Byway"],
    "Dinosaur Diamond": ["Dinosaur Diamond Prehistoric Highway"],
    "Flaming Gorge - Green River Basin Scenic Byway": ["Flaming Gorge-Uintas National Scenic Byway"],
    "Hocking Hills Scenic Byway": ["Hocking Hills Scenic Byway"],
    "Illinois River Road": ["Illinois River Road National Scenic Byway"],
    "Journey Through Hallowed Ground Byway": ["Journey Through Hallowed Ground"],
    "Lincoln Highway Heritage Byway": ["Lincoln Highway Scenic & Historic Byway"],
    "Loess Hills National Scenic Byway": ["Loess Hills Scenic Byway"],
    "Merritt Parkway": ["Merritt Parkway Scenic Byway"],
    "Mohawk Trail Scenic Byway": ["Mohawk Trail - MA"],
    "Mountains to Sound Greenway": ["Mountains to Sound Greenway Byway"],
    "Mountains to Sound Greenway - I-90": ["Mountains to Sound Greenway"],
    "Nebo Loop Scenic Byway": ["Mount Nebo Scenic Byway"],
    "Ohio River Scenic Byway": ["Ohio River Scenic Route"],
    "Old Frankfort Pike Historic and Scenic Byway": ["Old Frankfort Pike"],
    "Peter Norbeck Scenic Byway": ["Peter Norbeck Byway"],
    "River of Lakes Heritage Corridor": ["River of Lakes Heritage Corridor Scenic Highway"],
    "Russell-Brasstown Scenic Byway": ["Russell Brasstown Scenic Byway"],
    "Russell-Brasstown National Scenic Byway": ["Russell Brasstown Scenic Byway"],
    "Sandhills Journey Scenic Byway": ["Sandhills Journey"],
    "Santa Fe Trail": ["Santa Fe Trail Scenic and Historic Byway"],
    "Scenic Highway 30A": ["Scenic Highway 30-A"],
    "Scenic Highway of Legends": ["Highway of Legends"],
    "Silver Thread Colorado Scenic & Historic Byway": ["Silver Thread Scenic Byway"],
    "Sky Island Scenic Byway": ["Catalina Highway"],
    "Strait of Juan de Fuca Highway - SR 112": ["Strait of Juan de Fuca Highway - Washington State Route 112"],
    "The Energy Loop: Huntington & Eccles Canyons Scenic Byways": ["Energy Loop"],
    "The Battle Road Scenic Byway": ["Battle Road Scenic Byway"],
    "The Energy Loop: Huntington/Eccles Canyons Scenic Byway": [
        "Huntington/Eccles Canyons Scenic Byway",
        "Energy Loop",
    ],
    "The George Parks Highway Scenic Byway": ["George Parks Highway Scenic Byway"],
    "The High Road to Taos": ["High Road to Taos"],
    "Top of the Rockies": ["Top of the Rockies Scenic Byway"],
    "Trail of the Ancients": ["Trail of the Ancients Scenic Byway"],
    "Western Highlands Scenic Byway": ["Highlands Scenic Byway"],
    "White Pass Scenic Byway": ["White Pass Byway"],
    "Zion Scenic Byway": ["Zion Park Scenic Byway (UT-9)"],
}


def clean_text(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", value)).split())


def parse_fhwa_byways() -> list[Byway]:
    page = base.fetch_text(base.FHWA_BYWAYS_URL)
    pattern = re.compile(
        r'<li class="byway media" id="byway-(?P<id>\d+)">.*?'
        r'<h2 class="media-heading">\s*'
        r'<a href="(?P<href>[^"]+)">(?P<name>[^<]+)</a>\s*</h2>\s*'
        r'<div class="meta">(?P<meta>.*?)</div>',
        re.S,
    )
    byways: list[Byway] = []
    for match in pattern.finditer(page):
        meta = match.group("meta")
        is_aar = "all-american-road" in meta
        is_nsb = "national-scenic-byway" in meta
        if not (is_aar or is_nsb):
            continue

        designation_part, _, state_part = meta.partition("•")
        designations = clean_text(designation_part).replace(" ,", ",")
        states = clean_text(state_part)
        href = html.unescape(match.group("href"))
        byways.append(
            Byway(
                fhwa_id=match.group("id"),
                name=html.unescape(match.group("name")).strip(),
                states=states,
                designations=designations,
                is_all_american_road=is_aar,
                is_national_scenic_byway=is_nsb,
                source_url=urllib.parse.urljoin(base.FHWA_BYWAYS_URL, href),
            )
        )
    byways.sort(key=lambda item: item.name)
    return byways


def geojson_line_strings_with_states(geojson: dict[str, object]) -> tuple[list[list[tuple[float, float]]], list[str]]:
    lines: list[list[tuple[float, float]]] = []
    states: list[str] = []
    for feature in geojson.get("features", []):
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry") or {}
        properties = feature.get("properties") or {}
        if not isinstance(geometry, dict) or not isinstance(properties, dict):
            continue
        state = str(properties.get("State") or "").strip()
        gtype = geometry.get("type")
        coords = geometry.get("coordinates") or []
        if gtype == "LineString":
            line = [(float(x), float(y)) for x, y, *_ in coords]
            if len(line) >= 2:
                lines.append(line)
                states.append(state)
        elif gtype == "MultiLineString":
            for part in coords:
                line = [(float(x), float(y)) for x, y, *_ in part]
                if len(line) >= 2:
                    lines.append(line)
                    states.append(state)
    return lines, states


def find_arcgis_geometry_with_states(byway: Byway) -> base.GeometryResult | None:
    candidates = [byway.name, *base.ARCGIS_ALIASES.get(byway.name, [])]
    norm_candidates = {base.normalize_name(name) for name in candidates}

    for layer_id, field in ARCGIS_LAYER_NAME_FIELD.items():
        try:
            data = base.arcgis_query(layer_id, base.arcgis_where(field, candidates), "*", True)
        except Exception:
            continue
        lines, line_states = geojson_line_strings_with_states(data)
        if lines:
            result = base.GeometryResult(
                source="ArcGIS",
                source_detail=f"FeatureServer layer {layer_id}",
                line_strings=lines,
                status="matched",
            )
            setattr(result, "line_states", line_states)
            return result

    for layer_id, field in ARCGIS_LAYER_NAME_FIELD.items():
        try:
            data = base.arcgis_query(layer_id, "1=1", field, False)
        except Exception:
            continue
        names = sorted(
            {
                feature.get("attributes", {}).get(field)
                for feature in data.get("features", [])
                if feature.get("attributes", {}).get(field)
            }
        )
        matched = [name for name in names if base.normalize_name(name) in norm_candidates]
        if not matched:
            continue
        try:
            geom = base.arcgis_query(layer_id, base.arcgis_where(field, matched), "*", True)
        except Exception:
            continue
        lines, line_states = geojson_line_strings_with_states(geom)
        if lines:
            result = base.GeometryResult(
                source="ArcGIS",
                source_detail=f"FeatureServer layer {layer_id}; normalized name match",
                line_strings=lines,
                status="matched",
            )
            setattr(result, "line_states", line_states)
            return result
    return None


def get_geometry(byway: Byway) -> base.GeometryResult:
    old_aliases = {key: list(value) for key, value in base.ARCGIS_ALIASES.items()}
    try:
        base.ARCGIS_ALIASES.update(EXTRA_ARCGIS_ALIASES)
        result = find_arcgis_geometry_with_states(byway)
        if result and result.line_strings:
            assign_state(byway, result)
            return result
        result = base.get_geometry(byway)
        if result.line_strings:
            assign_state(byway, result)
            return result
        route = custom_osrm_route(byway)
        if route:
            assign_state(byway, route)
            return route
        fallback = generic_overpass_query(byway)
        if fallback:
            assign_state(byway, fallback)
            return fallback
        assign_state(byway, result)
        return result
    finally:
        base.ARCGIS_ALIASES.clear()
        base.ARCGIS_ALIASES.update(old_aliases)


CUSTOM_OSRM_ROUTES = {
    "Bold Coast Scenic Byway": [
        (-67.8810, 44.5358),  # Milbridge
        (-67.4614, 44.7151),  # Machias
        (-66.9859, 44.8587),  # Lubec
        (-66.9857, 44.9062),  # Eastport
    ],
    "Boom or Bust Byway": [
        (-94.0434, 32.8860),  # Louisiana/Texas border near LA 2
        (-93.9877, 32.8715),  # Vivian
        (-93.6960, 32.9054),  # Plain Dealing
        (-93.0554, 32.7918),  # Homer
        (-92.8790, 32.7950),  # Lisbon / Lake Claiborne side
    ],
    "Katahdin Woods & Waters Scenic Byway": [
        (-68.9050, 45.9180),  # Baxter State Park / Togue Pond side
        (-68.7097, 45.6573),  # Millinocket
        (-68.5746, 45.6290),  # East Millinocket
        (-68.5314, 45.6087),  # Medway
        (-68.4467, 45.9964),  # Patten
    ],
    "Norris Freeway": [
        (-83.9336, 36.0062),  # Halls Crossroads
        (-84.0696, 36.1956),  # Norris
        (-84.1546, 36.2179),  # Rocky Top
    ],
    "Revolutionary Heritage Byway": [
        (-71.2818, 41.7304),  # Warren
        (-71.2662, 41.6770),  # Bristol
    ],
    "Sequatchie Valley Scenic Byway": [
        (-85.6261, 35.0740),  # Jasper / I-24 side
        (-85.3905, 35.3715),  # Dunlap
        (-85.1888, 35.6056),  # Pikeville
        (-85.0269, 35.9489),  # Crossville / I-40 side
    ],
    "St. John Valley Cultural Byway/Fish River Scenic Byway": [
        (-67.9450, 47.0670),  # Hamlin / US 1 side
        (-67.9360, 47.1589),  # Van Buren
        (-68.3270, 47.3553),  # Madawaska
        (-68.5880, 47.2586),  # Fort Kent
        (-69.0520, 47.0800),  # Allagash / SR 161 side
        (-68.4780, 46.7770),  # Portage Lake / SR 11 side
    ],
    "Wisconsin Lake Superior Scenic Byway": [
        (-90.8840, 46.5870),  # Ashland / US 2 side
        (-90.8957, 46.6735),  # Washburn
        (-90.8185, 46.8113),  # Bayfield
        (-91.1010, 46.8535),  # Cornucopia
        (-91.3890, 46.7744),  # Port Wing
        (-91.5850, 46.5920),  # County H / Brule side
    ],
}


def custom_osrm_route(byway: Byway) -> base.GeometryResult | None:
    points = CUSTOM_OSRM_ROUTES.get(byway.name)
    if not points:
        return None
    coord_text = ";".join(f"{lon},{lat}" for lon, lat in points)
    params = urllib.parse.urlencode({"overview": "full", "geometries": "geojson", "steps": "false"})
    url = f"https://router.project-osrm.org/route/v1/driving/{coord_text}?{params}"
    try:
        data = json.loads(base.fetch_text(url, timeout=90))
    except Exception as exc:
        return base.GeometryResult(
            source="missing",
            source_detail=f"OSRM failed: {exc}",
            line_strings=[],
            status="missing",
        )
    if data.get("code") != "Ok" or not data.get("routes"):
        return base.GeometryResult(
            source="missing",
            source_detail=f"OSRM returned {data.get('code', 'no code')}",
            line_strings=[],
            status="missing",
        )
    coords = data["routes"][0]["geometry"]["coordinates"]
    line = [(float(lon), float(lat)) for lon, lat, *_ in coords]
    if len(line) < 2:
        return base.GeometryResult(
            source="missing",
            source_detail="OSRM returned no usable geometry",
            line_strings=[],
            status="missing",
        )
    return base.GeometryResult(
        source="OSM补齐",
        source_detail="OSRM public route over OSM road network using documented byway endpoints/waypoints",
        line_strings=[line],
        status="matched",
    )


def osm_search_terms(byway: Byway) -> list[str]:
    terms = [byway.name]
    cleaned = byway.name
    cleaned = re.sub(r"\([^)]*\)", "", cleaned)
    cleaned = re.sub(r"\b(National Scenic Byway|Scenic Byway|Historic and Scenic Byway|Historic Byway|Byway)\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\b(Route|SR|State Route)\s*\d+\b", "", cleaned, flags=re.I)
    cleaned = cleaned.replace("&", "and").replace("/", " ")
    cleaned = " ".join(cleaned.split(" - ")[0].split())
    if cleaned and cleaned not in terms:
        terms.append(cleaned)
    terms.extend(EXTRA_ARCGIS_ALIASES.get(byway.name, []))
    seen: set[str] = set()
    unique: list[str] = []
    for term in terms:
        term = term.strip()
        if len(term) >= 5 and term.lower() not in seen:
            seen.add(term.lower())
            unique.append(term)
    return unique[:4]


def generic_overpass_query(byway: Byway) -> base.GeometryResult | None:
    for term in osm_search_terms(byway):
        escaped = re.escape(term).replace(r"\ ", ".*")
        query = f"""
        [out:json][timeout:60];
        (
          relation["type"="route"]["name"~"{escaped}",i];
          relation["type"="route"]["ref"~"{escaped}",i];
          way["highway"]["name"~"{escaped}",i];
        );
        out geom;
        """
        last_error = ""
        for endpoint in base.OVERPASS_URLS:
            try:
                data = json.loads(base.post_form(endpoint, {"data": query}, timeout=90))
                lines: list[list[tuple[float, float]]] = []
                for element in data.get("elements", []):
                    if element.get("type") == "way" and element.get("geometry"):
                        line = [(float(pt["lon"]), float(pt["lat"])) for pt in element["geometry"]]
                        if len(line) >= 2:
                            lines.append(line)
                    for member in element.get("members", []):
                        if member.get("type") == "way" and member.get("geometry"):
                            line = [(float(pt["lon"]), float(pt["lat"])) for pt in member["geometry"]]
                            if len(line) >= 2:
                                lines.append(line)
                if lines:
                    return base.GeometryResult(
                        source="OSM补齐",
                        source_detail=f"{endpoint}; name search: {term}",
                        line_strings=lines,
                        status="matched",
                    )
            except Exception as exc:
                last_error = str(exc)
                time.sleep(1)
        if last_error:
            continue
    return None


def add_text(parent: ET.Element, tag: str, text: str) -> ET.Element:
    child = ET.SubElement(parent, tag)
    child.text = text
    return child


def add_extended_data(parent: ET.Element, values: dict[str, str]) -> None:
    extended = ET.SubElement(parent, "ExtendedData")
    for key, value in values.items():
        data = ET.SubElement(extended, "Data", {"name": key})
        add_text(data, "value", value)


def haversine_meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1 = a
    lon2, lat2 = b
    radius = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    value = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def line_length_meters(line: list[tuple[float, float]]) -> float:
    return sum(haversine_meters(line[index], line[index + 1]) for index in range(len(line) - 1))


def primary_fhwa_state(byway: Byway) -> str:
    return byway.states.split(",")[0].strip() if byway.states else "Unknown"


def fhwa_state_candidates(byway: Byway) -> set[str]:
    return {state.strip() for state in byway.states.split(",") if state.strip()}


def assign_state(byway: Byway, result: base.GeometryResult) -> None:
    line_states = getattr(result, "line_states", None)
    if not line_states or len(line_states) != len(result.line_strings):
        assigned = primary_fhwa_state(byway)
        setattr(result, "assigned_state", assigned)
        setattr(result, "state_assignment_method", "FHWA first state fallback")
        setattr(result, "state_length_summary", "")
        return

    candidates = fhwa_state_candidates(byway)
    totals: dict[str, float] = {}
    for line, state in zip(result.line_strings, line_states):
        state = str(state or "").strip()
        if not state:
            continue
        if candidates and state not in candidates:
            continue
        totals[state] = totals.get(state, 0.0) + line_length_meters(line)

    if not totals:
        assigned = primary_fhwa_state(byway)
        setattr(result, "assigned_state", assigned)
        setattr(result, "state_assignment_method", "FHWA first state fallback")
        setattr(result, "state_length_summary", "")
        return

    assigned = max(totals.items(), key=lambda item: item[1])[0]
    summary = "; ".join(f"{state}:{meters / 1609.344:.1f}mi" for state, meters in sorted(totals.items(), key=lambda item: item[1], reverse=True))
    setattr(result, "assigned_state", assigned)
    setattr(result, "state_assignment_method", "ArcGIS longest geometry length by State")
    setattr(result, "state_length_summary", summary)


def endpoint_distance(line_a: list[tuple[float, float]], line_b: list[tuple[float, float]]) -> float:
    endpoints_a = (line_a[0], line_a[-1])
    endpoints_b = (line_b[0], line_b[-1])
    return min(haversine_meters(a, b) for a in endpoints_a for b in endpoints_b)


def continuous_components(lines: list[list[tuple[float, float]]]) -> list[list[int]]:
    parent = list(range(len(lines)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left in range(len(lines)):
        for right in range(left + 1, len(lines)):
            if endpoint_distance(lines[left], lines[right]) <= CONTINUITY_THRESHOLD_METERS:
                union(left, right)

    groups: dict[int, list[int]] = {}
    for index in range(len(lines)):
        groups.setdefault(find(index), []).append(index)
    return sorted(groups.values(), key=lambda group: min(group))


def endpoint_clusters(points: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    clusters: list[list[tuple[float, float]]] = []
    for point in points:
        for cluster in clusters:
            if any(haversine_meters(point, existing) <= CONTINUITY_THRESHOLD_METERS for existing in cluster):
                cluster.append(point)
                break
        else:
            clusters.append([point])
    return clusters


def representative_point(cluster: list[tuple[float, float]]) -> tuple[float, float]:
    lon = sum(point[0] for point in cluster) / len(cluster)
    lat = sum(point[1] for point in cluster) / len(cluster)
    return (lon, lat)


def component_markers(
    byway: Byway,
    result: base.GeometryResult,
    place_cache: dict[str, dict[str, str]],
) -> list[EndpointMarker]:
    markers: list[EndpointMarker] = []
    components = continuous_components(result.line_strings)
    multi_part = len(components) > 1
    for part_index, component in enumerate(components, start=1):
        endpoints = []
        for line_index in component:
            line = result.line_strings[line_index]
            endpoints.extend([line[0], line[-1]])
        clusters = endpoint_clusters(endpoints)
        is_loop = len(clusters) <= 1

        if is_loop:
            point = representative_point(clusters[0]) if clusters else result.line_strings[component[0]][0]
            place_name, full_place = reverse_place(point, place_cache)
            markers.append(
                EndpointMarker(
                    part_index=part_index,
                    marker_type="Start / End" if not multi_part else f"Part {part_index} Start / End",
                    point=point,
                    place_name=place_name,
                    full_place=full_place,
                )
            )
            continue

        farthest = (endpoints[0], endpoints[-1])
        farthest_distance = -1.0
        for left_index, left in enumerate(endpoints):
            for right in endpoints[left_index + 1 :]:
                distance = haversine_meters(left, right)
                if distance > farthest_distance:
                    farthest = (left, right)
                    farthest_distance = distance

        start_label = "Start" if not multi_part else f"Part {part_index} Start"
        end_label = "End" if not multi_part else f"Part {part_index} End"
        for marker_type, point in ((start_label, farthest[0]), (end_label, farthest[1])):
            place_name, full_place = reverse_place(point, place_cache)
            markers.append(
                EndpointMarker(
                    part_index=part_index,
                    marker_type=marker_type,
                    point=point,
                    place_name=place_name,
                    full_place=full_place,
                )
            )
    return markers


def load_place_cache() -> dict[str, dict[str, str]]:
    if not PLACE_CACHE.exists():
        return {}
    try:
        return json.loads(PLACE_CACHE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_place_cache(cache: dict[str, dict[str, str]]) -> None:
    PLACE_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def place_cache_key(point: tuple[float, float]) -> str:
    lon, lat = point
    return f"{lat:.5f},{lon:.5f}"


def fallback_place(point: tuple[float, float]) -> tuple[str, str]:
    lon, lat = point
    text = f"{lat:.6f}, {lon:.6f}"
    return text, text


def short_place_name(data: dict[str, object], point: tuple[float, float]) -> tuple[str, str]:
    address = data.get("address") if isinstance(data.get("address"), dict) else {}
    assert isinstance(address, dict)
    candidates = [
        address.get("city"),
        address.get("town"),
        address.get("village"),
        address.get("municipality"),
        address.get("hamlet"),
        address.get("locality"),
        address.get("suburb"),
        address.get("county"),
        address.get("state"),
        data.get("name"),
    ]
    full_place = str(data.get("display_name") or "").strip()
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text, full_place or text
    short, full = fallback_place(point)
    return short, full_place or full


def reverse_place(point: tuple[float, float], cache: dict[str, dict[str, str]]) -> tuple[str, str]:
    key = place_cache_key(point)
    cached = cache.get(key)
    if cached and cached.get("version") == "city-v2":
        return cached.get("place_name") or fallback_place(point)[0], cached.get("full_place") or fallback_place(point)[1]

    lon, lat = point
    params = urllib.parse.urlencode({"format": "jsonv2", "lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "zoom": "14", "addressdetails": "1"})
    url = f"https://nominatim.openstreetmap.org/reverse?{params}"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "AmericasBywaysKML/1.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        place_name, full_place = short_place_name(data, point)
        time.sleep(1.1)
    except Exception:
        place_name, full_place = fallback_place(point)
    cache[key] = {"place_name": place_name, "full_place": full_place, "version": "city-v2"}
    save_place_cache(cache)
    return place_name, full_place


def style_id(byway: Byway) -> str:
    return "all-american-road-purple" if byway.is_all_american_road else "national-scenic-byway-green"


def write_kml(byways: list[Byway], geometries: dict[str, base.GeometryResult]) -> None:
    ET.register_namespace("", "http://www.opengis.net/kml/2.2")
    place_cache = load_place_cache()
    kml = ET.Element("{http://www.opengis.net/kml/2.2}kml")
    document = ET.SubElement(kml, "Document")
    add_text(document, "name", "America's Byways - FHWA current list")
    add_text(
        document,
        "description",
        "All current FHWA America's Byways. All-American Roads are #ff00ff 5 pt; National Scenic Byways are #00ff00 2 pt.",
    )

    aar_style = ET.SubElement(document, "Style", {"id": "all-american-road-purple"})
    aar_line = ET.SubElement(aar_style, "LineStyle")
    add_text(aar_line, "color", "ffff00ff")
    add_text(aar_line, "width", "5")

    nsb_style = ET.SubElement(document, "Style", {"id": "national-scenic-byway-green"})
    nsb_line = ET.SubElement(nsb_style, "LineStyle")
    add_text(nsb_line, "color", "ff00ff00")
    add_text(nsb_line, "width", "2")

    star_style = ET.SubElement(document, "Style", {"id": "endpoint-star"})
    icon_style = ET.SubElement(star_style, "IconStyle")
    add_text(icon_style, "scale", "1.1")
    icon = ET.SubElement(icon_style, "Icon")
    add_text(icon, "href", "http://maps.google.com/mapfiles/kml/shapes/star.png")

    state_names = sorted({str(getattr(geometries[byway.fhwa_id], "assigned_state", primary_fhwa_state(byway)) or "Unknown") for byway in byways})
    state_folders: dict[str, ET.Element] = {}
    for state_name in state_names:
        state_folder = ET.SubElement(document, "Folder")
        add_text(state_folder, "name", state_name)
        state_folders[state_name] = state_folder

    for byway in byways:
        result = geometries[byway.fhwa_id]
        assigned_state = str(getattr(result, "assigned_state", primary_fhwa_state(byway)) or "Unknown")
        state_assignment_method = str(getattr(result, "state_assignment_method", "FHWA first state fallback"))
        state_length_summary = str(getattr(result, "state_length_summary", ""))
        folder = ET.SubElement(state_folders[assigned_state], "Folder")
        add_text(folder, "name", byway.name)
        add_text(
            folder,
            "description",
            (
                f"Designations: {byway.designations}\n"
                f"States: {byway.states}\n"
                f"Assigned state: {assigned_state}\n"
                f"State assignment: {state_assignment_method}\n"
                f"Geometry source: {result.source} ({result.source_detail})\n"
                f"FHWA: {byway.source_url}"
            ),
        )
        add_extended_data(
            folder,
            {
                "fhwa_id": byway.fhwa_id,
                "name": byway.name,
                "states": byway.states,
                "assigned_state": assigned_state,
                "state_assignment_method": state_assignment_method,
                "state_length_summary": state_length_summary,
                "designations": byway.designations,
                "is_all_american_road": str(byway.is_all_american_road).lower(),
                "is_national_scenic_byway": str(byway.is_national_scenic_byway).lower(),
                "geometry_source": result.source,
                "geometry_source_detail": result.source_detail,
                "source_url": byway.source_url,
            },
        )

        route_placemark = ET.SubElement(folder, "Placemark")
        add_text(route_placemark, "name", byway.name)
        add_text(route_placemark, "styleUrl", f"#{style_id(byway)}")
        add_text(
            route_placemark,
            "description",
            (
                f"Designations: {byway.designations}\n"
                f"States: {byway.states}\n"
                f"Assigned state: {assigned_state}\n"
                f"LineString count: {len(result.line_strings)}\n"
                f"Geometry source: {result.source} ({result.source_detail})"
            ),
        )
        add_extended_data(
            route_placemark,
            {
                "fhwa_id": byway.fhwa_id,
                "name": byway.name,
                "states": byway.states,
                "assigned_state": assigned_state,
                "state_assignment_method": state_assignment_method,
                "state_length_summary": state_length_summary,
                "designations": byway.designations,
                "geometry_source": result.source,
                "geometry_source_detail": result.source_detail,
                "source_url": byway.source_url,
                "line_string_count": str(len(result.line_strings)),
            },
        )
        multi_geometry = ET.SubElement(route_placemark, "MultiGeometry")
        for line in result.line_strings:
            line_string = ET.SubElement(multi_geometry, "LineString")
            add_text(line_string, "tessellate", "1")
            add_text(line_string, "coordinates", " ".join(f"{lon:.9f},{lat:.9f},0" for lon, lat in line))

        markers = component_markers(byway, result, place_cache)
        for marker in markers:
            lon, lat = marker.point
            placemark = ET.SubElement(folder, "Placemark")
            add_text(placemark, "name", f"{byway.name} - {marker.marker_type}: {marker.place_name}")
            add_text(placemark, "styleUrl", "#endpoint-star")
            add_text(
                placemark,
                "description",
                (
                    f"Byway: {byway.name}\n"
                    f"{marker.marker_type}: {marker.place_name}\n"
                    f"Coordinates: {lat:.6f}, {lon:.6f}\n"
                    f"Full place: {marker.full_place}\n"
                    f"Geometry source: {result.source} ({result.source_detail})"
                ),
            )
            add_extended_data(
                placemark,
                {
                    "fhwa_id": byway.fhwa_id,
                    "name": byway.name,
                    "states": byway.states,
                    "assigned_state": assigned_state,
                    "designations": byway.designations,
                    "part_index": str(marker.part_index),
                    "marker_type": marker.marker_type,
                    "place_name": marker.place_name,
                    "full_place": marker.full_place,
                    "latitude": f"{lat:.6f}",
                    "longitude": f"{lon:.6f}",
                    "geometry_source": result.source,
                    "source_url": byway.source_url,
                },
            )
            point = ET.SubElement(placemark, "Point")
            add_text(point, "coordinates", f"{lon:.9f},{lat:.9f},0")

    tree = ET.ElementTree(kml)
    ET.indent(tree, space="  ")
    tree.write(OUT_KML, encoding="utf-8", xml_declaration=True)


def write_reports(byways: list[Byway], geometries: dict[str, base.GeometryResult]) -> None:
    with OUT_LIST.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "fhwa_id",
                "name",
                "states",
                "designations",
                "is_all_american_road",
                "is_national_scenic_byway",
                "source_url",
            ],
        )
        writer.writeheader()
        for byway in byways:
            writer.writerow(
                {
                    "fhwa_id": byway.fhwa_id,
                    "name": byway.name,
                    "states": byway.states,
                    "designations": byway.designations,
                    "is_all_american_road": byway.is_all_american_road,
                    "is_national_scenic_byway": byway.is_national_scenic_byway,
                    "source_url": byway.source_url,
                }
            )

    with OUT_REPORT.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "fhwa_id",
                "name",
                "states",
                "assigned_state",
                "state_assignment_method",
                "state_length_summary",
                "designations",
                "style",
                "geometry_source",
                "geometry_source_detail",
                "segment_count",
                "continuous_part_count",
                "endpoint_marker_count",
                "point_count",
                "status",
                "source_url",
            ],
        )
        writer.writeheader()
        for byway in byways:
            result = geometries[byway.fhwa_id]
            continuous_part_count = len(continuous_components(result.line_strings)) if result.line_strings else 0
            endpoint_marker_count = len(component_markers(byway, result, load_place_cache())) if result.line_strings else 0
            assigned_state = str(getattr(result, "assigned_state", primary_fhwa_state(byway)) or "Unknown")
            state_assignment_method = str(getattr(result, "state_assignment_method", "FHWA first state fallback"))
            state_length_summary = str(getattr(result, "state_length_summary", ""))
            writer.writerow(
                {
                    "fhwa_id": byway.fhwa_id,
                    "name": byway.name,
                    "states": byway.states,
                    "assigned_state": assigned_state,
                    "state_assignment_method": state_assignment_method,
                    "state_length_summary": state_length_summary,
                    "designations": byway.designations,
                    "style": style_id(byway),
                    "geometry_source": result.source,
                    "geometry_source_detail": result.source_detail,
                    "segment_count": len(result.line_strings),
                    "continuous_part_count": continuous_part_count,
                    "endpoint_marker_count": endpoint_marker_count,
                    "point_count": sum(len(line) for line in result.line_strings),
                    "status": result.status,
                    "source_url": byway.source_url,
                }
            )


def validate_outputs(byways: list[Byway], geometries: dict[str, base.GeometryResult]) -> None:
    if len(byways) != 184:
        raise RuntimeError(f"Expected 184 FHWA America's Byways, found {len(byways)}")
    aar_count = sum(1 for item in byways if item.is_all_american_road)
    nsb_count = sum(1 for item in byways if item.is_national_scenic_byway)
    if aar_count != 38 or nsb_count != 148:
        raise RuntimeError(f"Unexpected designation counts: AAR={aar_count}, NSB={nsb_count}")
    root = ET.parse(OUT_KML).getroot()
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    document = root.find("k:Document", ns)
    if document is None:
        raise RuntimeError("KML missing Document")
    state_folders = document.findall("k:Folder", ns)
    byway_folders = []
    for state_folder in state_folders:
        byway_folders.extend(state_folder.findall("k:Folder", ns))
    route_placemarks = [placemark for placemark in root.findall(".//k:Placemark", ns) if placemark.find("k:MultiGeometry", ns) is not None]
    line_strings = root.findall(".//k:LineString", ns)
    points = root.findall(".//k:Point", ns)
    expected_line_strings = sum(len(result.line_strings) for result in geometries.values())
    if not state_folders:
        raise RuntimeError("Expected state Folder elements under Document")
    if len(byway_folders) != 184:
        raise RuntimeError(f"Expected 184 Byway Folder elements, found {len(byway_folders)}")
    if len(route_placemarks) != 184:
        raise RuntimeError(f"Expected 184 route MultiGeometry placemarks, found {len(route_placemarks)}")
    if len(line_strings) != expected_line_strings:
        raise RuntimeError(f"Expected {expected_line_strings} LineString elements, found {len(line_strings)}")
    for point in points:
        coords = point.find("k:coordinates", ns)
        if coords is None or not (coords.text or "").strip():
            raise RuntimeError("Endpoint Point missing coordinates")
    for byway in byways:
        for line in geometries[byway.fhwa_id].line_strings:
            for lon, lat in line:
                if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                    raise RuntimeError(f"Invalid coordinate for {byway.name}: {lon},{lat}")


def main() -> int:
    byways = parse_fhwa_byways()
    geometries: dict[str, base.GeometryResult] = {}
    for index, byway in enumerate(byways, start=1):
        print(f"[{index:03d}/{len(byways)}] {byway.name}", flush=True)
        geometries[byway.fhwa_id] = get_geometry(byway)

    write_kml(byways, geometries)
    write_reports(byways, geometries)
    validate_outputs(byways, geometries)

    matched = sum(1 for item in geometries.values() if item.line_strings)
    missing = len(byways) - matched
    print(f"Wrote {OUT_KML} with {matched} routes containing geometry; {missing} routes missing geometry.")
    print(f"Wrote {OUT_REPORT} and {OUT_LIST}.")
    return 0 if missing == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
