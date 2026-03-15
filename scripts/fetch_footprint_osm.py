#!/usr/bin/env python3
"""Fetch a building footprint from OpenStreetMap via Overpass."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from shapely.geometry import Point, Polygon, MultiPolygon, mapping
from shapely.ops import transform
import pyproj


OVERPASS_DEFAULT = "https://overpass-api.de/api/interpreter"
USER_AGENT = "lidar-sf/0.1 (local script)"


@dataclass
class Candidate:
    feature: Dict[str, Any]
    area_m2: float
    contains_point: bool
    centroid_dist_m: float
    name_match: int


def slugify(value: str) -> str:
    out = []
    for ch in value.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_"):
            out.append("_")
    slug = "".join(out)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_") or "building"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch a building footprint from OSM.")
    parser.add_argument("--name", required=True, help="Building name for matching and labeling.")
    parser.add_argument("--lat", type=float, required=True, help="Latitude in WGS84.")
    parser.add_argument("--lon", type=float, required=True, help="Longitude in WGS84.")
    parser.add_argument("--radius-m", type=float, default=200.0, help="Search radius in meters.")
    parser.add_argument("--overpass", default=OVERPASS_DEFAULT, help="Overpass API endpoint.")
    parser.add_argument("--out", default=None, help="Output GeoJSON path.")
    return parser.parse_args()


def build_query(lat: float, lon: float, radius_m: float) -> str:
    return f"""
[out:json][timeout:25];
(
  way["building"](around:{radius_m},{lat},{lon});
  relation["building"](around:{radius_m},{lat},{lon});
);
out geom;
""".strip()


def close_ring(coords: List[List[float]]) -> List[List[float]]:
    if not coords:
        return coords
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    return coords


def polygon_from_coords(coords: List[Dict[str, float]]) -> Optional[Polygon]:
    if len(coords) < 3:
        return None
    ring = [[c["lon"], c["lat"]] for c in coords]
    ring = close_ring(ring)
    if len(ring) < 4:
        return None
    return Polygon(ring)


def geometry_from_element(element: Dict[str, Any]) -> Optional[Polygon | MultiPolygon]:
    el_type = element.get("type")
    if el_type == "way":
        geom = element.get("geometry")
        if not geom:
            return None
        return polygon_from_coords(geom)

    if el_type == "relation":
        members = element.get("members", [])
        outers = []
        inners = []
        for mem in members:
            role = mem.get("role")
            geom = mem.get("geometry")
            if not geom:
                continue
            ring = [[c["lon"], c["lat"]] for c in geom]
            ring = close_ring(ring)
            if len(ring) < 4:
                continue
            if role == "inner":
                inners.append(ring)
            else:
                outers.append(ring)
        if not outers:
            return None
        polys = []
        for outer in outers:
            polys.append(Polygon(outer, holes=inners or None))
        if len(polys) == 1:
            return polys[0]
        return MultiPolygon(polys)

    return None


def name_match_score(tags: Dict[str, Any], query: str) -> int:
    if not tags:
        return 0
    name = tags.get("name") or ""
    alt = tags.get("alt_name") or ""
    brand = tags.get("brand") or ""
    all_text = " ".join([name, alt, brand]).lower()
    query_lower = query.lower()
    if not all_text:
        return 0
    if query_lower in all_text:
        return 2
    tokens = [t for t in re.split(r"\W+", query_lower) if t]
    for tok in tokens:
        if tok in all_text:
            return 1
    return 0


def select_candidate(candidates: List[Candidate]) -> Candidate:
    if not candidates:
        raise ValueError("No candidate geometries found in OSM results.")
    candidates.sort(
        key=lambda c: (
            -c.name_match,
            -int(c.contains_point),
            -c.area_m2,
            c.centroid_dist_m,
        )
    )
    return candidates[0]


def main() -> int:
    args = parse_args()
    slug = slugify(args.name)
    out_path = Path(args.out) if args.out else Path("data/footprints") / f"{slug}_osm.geojson"
    candidates_path = out_path.with_name(f"{slug}_osm_candidates.geojson")

    query = build_query(args.lat, args.lon, args.radius_m)
    resp = requests.post(args.overpass, data=query.encode("utf-8"), headers={"User-Agent": USER_AGENT}, timeout=60)
    resp.raise_for_status()
    payload = resp.json()

    elements = payload.get("elements", [])
    if not elements:
        print("No elements returned from Overpass.")
        return 2

    point = Point(args.lon, args.lat)
    transformer = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)

    candidates: List[Candidate] = []
    features: List[Dict[str, Any]] = []

    for element in elements:
        geom = geometry_from_element(element)
        if geom is None or geom.is_empty:
            continue
        tags = element.get("tags", {})
        geom_3857 = transform(transformer.transform, geom)
        area_m2 = float(geom_3857.area)
        centroid = geom_3857.centroid
        point_3857 = transform(transformer.transform, point)
        dist = float(centroid.distance(point_3857))
        contains = geom.contains(point)
        match = name_match_score(tags, args.name)

        feature = {
            "type": "Feature",
            "geometry": mapping(geom),
            "properties": {
                **tags,
                "osm_type": element.get("type"),
                "osm_id": element.get("id"),
                "candidate_area_m2": area_m2,
                "candidate_contains_point": contains,
                "candidate_centroid_distance_m": dist,
                "candidate_name_match": match,
            },
        }

        candidates.append(
            Candidate(
                feature=feature,
                area_m2=area_m2,
                contains_point=contains,
                centroid_dist_m=dist,
                name_match=match,
            )
        )
        features.append(feature)

    if not candidates:
        print("No valid building geometries after filtering.")
        return 2

    selected = select_candidate(candidates)

    selected_feature = selected.feature.copy()
    selected_feature["properties"] = {
        **selected_feature["properties"],
        "selected_name": args.name,
        "selected_source": "osm:overpass",
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"type": "FeatureCollection", "features": [selected_feature]}, indent=2))

    candidates_path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, indent=2))

    print(f"Saved footprint: {out_path}")
    print(f"Saved candidates: {candidates_path}")
    print(
        "Selected footprint info: "
        f"name_match={selected.name_match}, "
        f"contains_point={selected.contains_point}, "
        f"area_m2={selected.area_m2:.1f}, "
        f"centroid_distance_m={selected.centroid_dist_m:.1f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
