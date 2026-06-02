#!/usr/bin/env python3
"""
Generate a KML file for the current FHWA All-American Roads list.

Data policy:
- FHWA America's Byways is the authority for the route list.
- ArcGIS public scenic byways layers are the preferred line geometry source.
- OSM/Overpass is used only as a fallback and is clearly marked in metadata.
"""

from __future__ import annotations

import csv
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any


FHWA_BYWAYS_URL = "https://fhwaapps.fhwa.dot.gov/bywaysp/Byways"
ARCGIS_SERVICE = (
    "https://services7.arcgis.com/yiuFazTjHE8F5gzQ/arcgis/rest/services/"
    "America_s_Scenic_Highways_and_Byways_WFL1/FeatureServer"
)
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

OUT_KML = Path("all_american_roads.kml")
OUT_REPORT = Path("coverage_report.csv")
OUT_LIST = Path("fhwa_all_american_roads_list.csv")


@dataclass(frozen=True)
class Byway:
    fhwa_id: str
    name: str
    states: str
    source_url: str


@dataclass
class GeometryResult:
    source: str
    source_detail: str
    line_strings: list[list[tuple[float, float]]]
    status: str


def fetch_text(url: str, timeout: int = 60, data: bytes | None = None) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; AllAmericanRoadsKML/1.0; "
            "+https://fhwaapps.fhwa.dot.gov/bywaysp/)"
        )
    }
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def post_form(url: str, params: dict[str, str], timeout: int = 90) -> str:
    body = urllib.parse.urlencode(params).encode("utf-8")
    return fetch_text(url, timeout=timeout, data=body)


def normalize_name(value: str) -> str:
    value = html.unescape(value).lower()
    value = value.replace("&", " and ")
    value = value.replace("'", "")
    value = value.replace("/", " ")
    value = re.sub(r"\([^)]*\)", " ", value)
    value = re.sub(r"\b(all american road|national scenic byway|scenic byway|byway|highway|road|route)\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def parse_fhwa_byways() -> list[Byway]:
    page = fetch_text(FHWA_BYWAYS_URL)
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
        if "all-american-road" not in meta:
            continue
        states = re.sub(r"<[^>]+>", "", meta)
        states = html.unescape(states)
        states = re.sub(r".*?•", "", states).strip()
        href = html.unescape(match.group("href"))
        source_url = urllib.parse.urljoin(FHWA_BYWAYS_URL, href)
        byways.append(
            Byway(
                fhwa_id=match.group("id"),
                name=html.unescape(match.group("name")).strip(),
                states=states,
                source_url=source_url,
            )
        )
    byways.sort(key=lambda item: item.name)
    return byways


ARCGIS_LAYER_NAME_FIELD = {
    1: "Name",  # All-American Roads
    2: "Byway_Name",  # National Scenic Byway
    3: "Byway_Name",  # Parkway
    4: "Byway_Name",  # National Forest Scenic Byway
    5: "Byway_Name",  # BLM Backcountry Byway
    6: "BYWAY_NAME",  # Other Scenic Byway
}


ARCGIS_ALIASES = {
    "Alaska Marine Highway": ["Alaska's Marine Highway"],
    "Chesapeake Country Scenic Byway": ["Chesapeake Country Byway"],
    "Flaming Gorge - Green River Basin Scenic Byway": ["Flaming Gorge-Uintas National Scenic Byway"],
    "Lincoln Highway Scenic & History Byway": ["Lincoln Highway"],
    "Pacific Coast Scenic Byway - Oregon": ["Pacific Coast Scenic Byway"],
    "Route 1 - Big Sur Coast Highway": ["Big Sur Coast Byway"],
    "Route 1 - San Luis Obispo North Coast Byway": ["San Luis Obispo North Coast Byway"],
    "Scenic Byway 12": ["Utah State Route 12 Scenic Byway"],
    "Trail Ridge Road/Beaver Meadow Road": ["Trail Ridge Road / Beaver Meadow Road"],
    "Woodward Avenue (M-1) - Automotive Heritage Trail": ["Woodward Avenue Automotive Heritage Trail"],
}


def arcgis_query(layer_id: int, where: str, out_fields: str, return_geometry: bool) -> dict[str, Any]:
    params = {
        "where": where,
        "outFields": out_fields,
        "returnGeometry": "true" if return_geometry else "false",
        "f": "geojson" if return_geometry else "json",
        "outSR": "4326",
        "resultRecordCount": "2000",
    }
    url = f"{ARCGIS_SERVICE}/{layer_id}/query?{urllib.parse.urlencode(params)}"
    return json.loads(fetch_text(url))


def arcgis_where(field: str, candidates: list[str]) -> str:
    parts = []
    for candidate in candidates:
        escaped = candidate.replace("'", "''")
        parts.append(f"{field} = '{escaped}'")
    return " OR ".join(parts)


def geojson_line_strings(geojson: dict[str, Any]) -> list[list[tuple[float, float]]]:
    lines: list[list[tuple[float, float]]] = []
    for feature in geojson.get("features", []):
        geometry = feature.get("geometry") or {}
        gtype = geometry.get("type")
        coords = geometry.get("coordinates") or []
        if gtype == "LineString":
            line = [(float(x), float(y)) for x, y, *_ in coords]
            if len(line) >= 2:
                lines.append(line)
        elif gtype == "MultiLineString":
            for part in coords:
                line = [(float(x), float(y)) for x, y, *_ in part]
                if len(line) >= 2:
                    lines.append(line)
    return lines


def find_arcgis_geometry(byway: Byway) -> GeometryResult | None:
    candidates = [byway.name, *ARCGIS_ALIASES.get(byway.name, [])]
    norm_candidates = {normalize_name(name) for name in candidates}

    for layer_id, field in ARCGIS_LAYER_NAME_FIELD.items():
        try:
            data = arcgis_query(layer_id, arcgis_where(field, candidates), "*", True)
        except Exception:
            continue
        lines = geojson_line_strings(data)
        if lines:
            return GeometryResult(
                source="ArcGIS",
                source_detail=f"FeatureServer layer {layer_id}",
                line_strings=lines,
                status="matched",
            )

    # Last ArcGIS pass: pull distinct names and query normalized exact matches.
    for layer_id, field in ARCGIS_LAYER_NAME_FIELD.items():
        try:
            data = arcgis_query(layer_id, "1=1", field, False)
        except Exception:
            continue
        names = sorted(
            {
                feature.get("attributes", {}).get(field)
                for feature in data.get("features", [])
                if feature.get("attributes", {}).get(field)
            }
        )
        matched = [name for name in names if normalize_name(name) in norm_candidates]
        if not matched:
            continue
        try:
            geom = arcgis_query(layer_id, arcgis_where(field, matched), "*", True)
        except Exception:
            continue
        lines = geojson_line_strings(geom)
        if lines:
            return GeometryResult(
                source="ArcGIS",
                source_detail=f"FeatureServer layer {layer_id}; normalized name match",
                line_strings=lines,
                status="matched",
            )
    return None


OSM_QUERIES = {
    "A1A Scenic & Historic Coastal Byway": '["name"~"A1A Scenic|A1A.*Historic",i]',
    "Door County Coastal Byway": '["name"~"Door County Coastal",i]',
    "Great River Road": '["name"~"Great River Road",i]',
    "Newfound Gap Road Byway": '["name"~"Newfound Gap",i]',
}


OSRM_ROUTES = {
    # Newfound Gap Road is the US 441 corridor through Great Smoky Mountains National Park.
    # The intermediate Newfound Gap point keeps the public OSM router on the intended pass.
    "Newfound Gap Road Byway": [
        (-83.5103, 35.6815),  # Gatlinburg / Sugarlands side
        (-83.4250, 35.6111),  # Newfound Gap
        (-83.3069, 35.5133),  # Cherokee / Oconaluftee side
    ],
}


def overpass_query(byway: Byway) -> GeometryResult | None:
    name_filter = OSM_QUERIES.get(byway.name)
    if not name_filter:
        return None
    query = f"""
    [out:json][timeout:60];
    (
      relation["type"="route"]["route"~"road|ferry"]{name_filter};
      way{ name_filter };
    );
    out geom;
    """
    last_error = ""
    for endpoint in OVERPASS_URLS:
        try:
            text = post_form(endpoint, {"data": query}, timeout=90)
            data = json.loads(text)
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
                return GeometryResult(
                    source="OSM补齐",
                    source_detail=endpoint,
                    line_strings=lines,
                    status="matched",
                )
        except Exception as exc:
            last_error = str(exc)
            time.sleep(2)
    return GeometryResult(
        source="missing",
        source_detail=f"Overpass failed or returned no geometry: {last_error}",
        line_strings=[],
        status="missing",
    )


def osrm_route(byway: Byway) -> GeometryResult | None:
    points = OSRM_ROUTES.get(byway.name)
    if not points:
        return None
    coord_text = ";".join(f"{lon},{lat}" for lon, lat in points)
    params = urllib.parse.urlencode(
        {
            "overview": "full",
            "geometries": "geojson",
            "steps": "false",
        }
    )
    url = f"https://router.project-osrm.org/route/v1/driving/{coord_text}?{params}"
    try:
        data = json.loads(fetch_text(url, timeout=90))
    except Exception as exc:
        return GeometryResult(
            source="missing",
            source_detail=f"OSRM failed: {exc}",
            line_strings=[],
            status="missing",
        )
    if data.get("code") != "Ok" or not data.get("routes"):
        return GeometryResult(
            source="missing",
            source_detail=f"OSRM returned {data.get('code', 'no code')}",
            line_strings=[],
            status="missing",
        )
    coords = data["routes"][0]["geometry"]["coordinates"]
    line = [(float(lon), float(lat)) for lon, lat, *_ in coords]
    if len(line) < 2:
        return GeometryResult(
            source="missing",
            source_detail="OSRM returned no usable geometry",
            line_strings=[],
            status="missing",
        )
    return GeometryResult(
        source="OSM补齐",
        source_detail="OSRM public route over OSM road network via Gatlinburg/Sugarlands, Newfound Gap, Cherokee/Oconaluftee",
        line_strings=[line],
        status="matched",
    )


def get_geometry(byway: Byway) -> GeometryResult:
    arcgis = find_arcgis_geometry(byway)
    if arcgis:
        return arcgis
    osm = overpass_query(byway)
    if osm and osm.line_strings:
        return osm
    osrm = osrm_route(byway)
    if osrm:
        return osrm
    if osm:
        return osm
    return GeometryResult(
        source="missing",
        source_detail="No ArcGIS match and no configured OSM fallback",
        line_strings=[],
        status="missing",
    )


def add_text(parent: ET.Element, tag: str, text: str) -> ET.Element:
    child = ET.SubElement(parent, tag)
    child.text = text
    return child


def add_extended_data(parent: ET.Element, values: dict[str, str]) -> None:
    extended = ET.SubElement(parent, "ExtendedData")
    for key, value in values.items():
        data = ET.SubElement(extended, "Data", {"name": key})
        add_text(data, "value", value)


def write_kml(byways: list[Byway], geometries: dict[str, GeometryResult]) -> None:
    ET.register_namespace("", "http://www.opengis.net/kml/2.2")
    kml = ET.Element("{http://www.opengis.net/kml/2.2}kml")
    document = ET.SubElement(kml, "Document")
    add_text(document, "name", "All-American Roads - FHWA current list")
    add_text(
        document,
        "description",
        "Route list from FHWA America's Byways. Geometry is sourced from public ArcGIS layers when available; OSM fallback geometry is marked.",
    )

    style = ET.SubElement(document, "Style", {"id": "all-american-road-line"})
    line_style = ET.SubElement(style, "LineStyle")
    add_text(line_style, "color", "ff0055ff")
    add_text(line_style, "width", "3")

    for byway in byways:
        result = geometries[byway.fhwa_id]
        folder = ET.SubElement(document, "Folder")
        add_text(folder, "name", byway.name)
        add_text(
            folder,
            "description",
            f"States: {byway.states}\nGeometry source: {result.source} ({result.source_detail})\nFHWA: {byway.source_url}",
        )
        add_extended_data(
            folder,
            {
                "fhwa_id": byway.fhwa_id,
                "name": byway.name,
                "states": byway.states,
                "geometry_source": result.source,
                "source_url": byway.source_url,
                "geometry_source_detail": result.source_detail,
            },
        )
        if not result.line_strings:
            continue
        for index, line in enumerate(result.line_strings, start=1):
            placemark = ET.SubElement(folder, "Placemark")
            add_text(placemark, "name", f"{byway.name} segment {index}")
            add_text(placemark, "styleUrl", "#all-american-road-line")
            add_extended_data(
                placemark,
                {
                    "fhwa_id": byway.fhwa_id,
                    "name": byway.name,
                    "states": byway.states,
                    "geometry_source": result.source,
                    "source_url": byway.source_url,
                },
            )
            line_string = ET.SubElement(placemark, "LineString")
            add_text(line_string, "tessellate", "1")
            coords = " ".join(f"{lon:.9f},{lat:.9f},0" for lon, lat in line)
            add_text(line_string, "coordinates", coords)

    tree = ET.ElementTree(kml)
    ET.indent(tree, space="  ")
    tree.write(OUT_KML, encoding="utf-8", xml_declaration=True)


def write_reports(byways: list[Byway], geometries: dict[str, GeometryResult]) -> None:
    with OUT_LIST.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=["fhwa_id", "name", "states", "source_url"])
        writer.writeheader()
        for byway in byways:
            writer.writerow(
                {
                    "fhwa_id": byway.fhwa_id,
                    "name": byway.name,
                    "states": byway.states,
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
                "geometry_source",
                "geometry_source_detail",
                "segment_count",
                "point_count",
                "status",
                "source_url",
            ],
        )
        writer.writeheader()
        for byway in byways:
            result = geometries[byway.fhwa_id]
            writer.writerow(
                {
                    "fhwa_id": byway.fhwa_id,
                    "name": byway.name,
                    "states": byway.states,
                    "geometry_source": result.source,
                    "geometry_source_detail": result.source_detail,
                    "segment_count": len(result.line_strings),
                    "point_count": sum(len(line) for line in result.line_strings),
                    "status": result.status,
                    "source_url": byway.source_url,
                }
            )


def validate_outputs(byways: list[Byway], geometries: dict[str, GeometryResult]) -> None:
    if len(byways) != 38:
        raise RuntimeError(f"Expected 38 FHWA All-American Roads, found {len(byways)}")
    ET.parse(OUT_KML)
    for byway in byways:
        result = geometries[byway.fhwa_id]
        for line in result.line_strings:
            for lon, lat in line:
                if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                    raise RuntimeError(f"Invalid coordinate for {byway.name}: {lon},{lat}")


def main() -> int:
    byways = parse_fhwa_byways()
    geometries: dict[str, GeometryResult] = {}
    for index, byway in enumerate(byways, start=1):
        print(f"[{index:02d}/{len(byways)}] {byway.name}", flush=True)
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
