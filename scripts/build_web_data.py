#!/usr/bin/env python3
"""Convert the existing Byways KML into a small index and per-route detail JSON.

Python standard library only. Run from any working directory:
    python scripts/build_web_data.py

The full source coordinates and original LineString boundaries are preserved in
detail files. Overview simplification is for display only; it never joins lines,
repairs source gaps, or changes the original KML.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
NS = {"k": "http://www.opengis.net/kml/2.2"}
METERS_PER_DEGREE = 111_195.0802


def extended(element: ET.Element) -> dict[str, str]:
    return {
        item.attrib["name"]: item.findtext("k:value", "", NS)
        for item in element.findall("k:ExtendedData/k:Data", NS)
    }


def coordinates(element: ET.Element) -> list[list[float]]:
    result = []
    for token in element.findtext("k:coordinates", "", NS).split():
        point = [float(value) for value in token.split(",")[:2]]
        if len(point) != 2 or not all(math.isfinite(value) for value in point):
            raise ValueError("Invalid source coordinate")
        if not (-180 <= point[0] <= 180 and -90 <= point[1] <= 90):
            raise ValueError("Source coordinate outside WGS84 limits")
        result.append(point)
    return result


def simplify_line(line: list[list[float]], tolerance: float) -> list[list[float]]:
    """Iterative Douglas-Peucker using a per-line equirectangular metric plane."""
    if len(line) <= 2:
        return [point[:] for point in line]
    latitude = (min(p[1] for p in line) + max(p[1] for p in line)) / 2
    x_scale = METERS_PER_DEGREE * math.cos(math.radians(latitude))
    origin_x, origin_y = line[0]
    projected = [((x - origin_x) * x_scale, (y - origin_y) * METERS_PER_DEGREE) for x, y in line]
    keep = {0, len(line) - 1}
    stack = [(0, len(line) - 1)]
    tolerance_squared = tolerance * tolerance
    while stack:
        first, last = stack.pop()
        if last - first < 2:
            continue
        ax, ay = projected[first]
        bx, by = projected[last]
        dx, dy = bx - ax, by - ay
        denominator = dx * dx + dy * dy
        largest, selected = -1.0, -1
        for index in range(first + 1, last):
            px, py = projected[index]
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denominator)) if denominator else 0.0
            distance = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if distance > largest:
                largest, selected = distance, index
        if largest > tolerance_squared:
            keep.add(selected)
            stack.extend(((first, selected), (selected, last)))
    return [[round(value, 6) for value in line[index]] for index in sorted(keep)]


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n", encoding="utf-8")


def build(tolerance: float, updated: str) -> dict[str, object]:
    kml_path = ROOT / "americas_byways.kml"
    tree = ET.parse(kml_path)
    source_report = {
        row["fhwa_id"]: row
        for row in csv.DictReader((ROOT / "americas_byways_coverage_report.csv").open(encoding="utf-8-sig", newline=""))
    }
    route_data: dict[str, tuple[dict[str, str], list[list[list[float]]], list[dict[str, object]]]] = {}
    endpoint_records: dict[str, list[dict[str, object]]] = {}
    for placemark in tree.findall(".//k:Placemark", NS):
        properties = extended(placemark)
        route_id = properties.get("fhwa_id")
        if not route_id:
            continue
        if not re.fullmatch(r"\d+", route_id):
            raise ValueError(f"Unsafe route ID: {route_id}")
        multi = placemark.find("k:MultiGeometry", NS)
        point_element = placemark.find("k:Point", NS)
        if multi is not None:
            if route_id in route_data:
                raise ValueError(f"Duplicate route: {route_id}")
            lines = [coordinates(line) for line in multi.findall(".//k:LineString", NS)]
            if not lines or any(len(line) < 2 for line in lines):
                raise ValueError(f"Missing or short route geometry: {route_id}")
            route_data[route_id] = (properties, lines, [])
        elif point_element is not None:
            points = coordinates(point_element)
            if len(points) != 1:
                raise ValueError(f"Invalid endpoint: {route_id}")
            endpoint_records.setdefault(route_id, []).append({
                "name": placemark.findtext("k:name", "", NS),
                "coords": points[0],
                "kind": properties.get("marker_type", ""),
                "placeName": properties.get("place_name", ""),
                "fullPlace": properties.get("full_place", ""),
            })
    if set(route_data) != set(source_report):
        raise ValueError("KML route IDs do not match coverage report")

    output = ROOT / "data"
    route_dir = output / "routes"
    route_dir.mkdir(parents=True, exist_ok=True)
    catalog = []
    source_points = overview_points = source_segments = endpoint_count = 0
    all_states: set[str] = set()
    for route_id, (properties, lines, _) in sorted(route_data.items(), key=lambda row: row[1][0]["name"].casefold()):
        source_row = source_report[route_id]
        point_count = sum(map(len, lines))
        endpoints = endpoint_records.get(route_id, [])
        if point_count != int(source_row["point_count"]) or len(lines) != int(source_row["segment_count"]):
            raise ValueError(f"Geometry count differs from coverage report: {route_id}")
        if len(endpoints) != int(source_row["endpoint_marker_count"]):
            raise ValueError(f"Endpoint count differs from coverage report: {route_id}")
        states = [state.strip() for state in properties["states"].split(",") if state.strip()]
        all_states.update(states)
        xmin = min(point[0] for line in lines for point in line)
        xmax = max(point[0] for line in lines for point in line)
        ymin = min(point[1] for line in lines for point in line)
        ymax = max(point[1] for line in lines for point in line)
        overview = [simplify_line(line, tolerance) for line in lines]
        overview_points += sum(map(len, overview))
        source_points += point_count
        source_segments += len(lines)
        endpoint_count += len(endpoints)
        note = "保留原始数据分段；概览线经过显示简化，选择路线后载入完整几何。路线资料不表示当前路况或可通行性。"
        if properties["geometry_source"] != "ArcGIS":
            note += " 此路线为OSM/OSRM补齐参照，并非FHWA逐米测绘中心线。"
        if properties["name"] == "Historic Route 66":
            note += " 特别说明：源ArcGIS的个别LineString内部存在可见直线跳接，本网页按原始数据保留，未将这些间断修成道路。"
        if properties["name"] == "Alaska Marine Highway":
            note += " 此路线包含海上渡轮航段，不能将其理解为连续陆路。"
        catalog.append({
            "id": route_id,
            "name": properties["name"],
            "states": states,
            "assignedState": properties["assigned_state"],
            "designation": "all-american-road" if "All-American Road" in properties["designations"] else "national-scenic-byway",
            "geometrySource": properties["geometry_source"],
            "geometrySourceDetail": properties.get("geometry_source_detail", ""),
            "sourceUrl": properties["source_url"],
            "pointCount": point_count,
            "segmentCount": len(lines),
            "endpointCount": len(endpoints),
            "bounds": [[ymin, xmin], [ymax, xmax]],
            "overview": overview,
            "detailUrl": f"data/routes/{route_id}.json",
            "note": note,
        })
        write_json(route_dir / f"{route_id}.json", {"id": route_id, "lines": lines, "endpoints": endpoints})

    all_american = sum(route["designation"] == "all-american-road" for route in catalog)
    meta = {
        "routeCount": len(catalog),
        "allAmericanCount": all_american,
        "nationalScenicCount": len(catalog) - all_american,
        "sourcePointCount": source_points,
        "sourceSegmentCount": source_segments,
        "endpointCount": endpoint_count,
        "overviewPointCount": overview_points,
        "overviewToleranceMeters": tolerance,
        "updated": updated,
        "sourceFile": "americas_byways.kml",
        "sourceNote": "网页数据从仓库内既有KML转换；updated表示网页整理日期，并非重新核验联邦名录的日期。",
    }
    write_json(output / "index.json", {"meta": meta, "states": sorted(all_states), "routes": catalog})
    return meta


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tolerance", type=float, default=100.0, help="Overview simplification tolerance in approximate meters")
    parser.add_argument("--updated", default="2026-11-15", help="Web conversion date, YYYY-MM-DD")
    arguments = parser.parse_args()
    if arguments.tolerance <= 0:
        parser.error("--tolerance must be positive")
    dt.date.fromisoformat(arguments.updated)
    print(json.dumps(build(arguments.tolerance, arguments.updated), ensure_ascii=False, indent=2))
