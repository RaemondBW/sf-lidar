#!/usr/bin/env python3
"""Fetch a single building footprint from DataSF by proximity.

Defaults to DataSF Building Footprints dataset (ynuv-fyni).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from shapely.geometry import shape, Point, mapping


DATASET_ID_DEFAULT = "ynuv-fyni"
DATASF_BASE = "https://data.sfgov.org"


@dataclass
class FeatureCandidate:
    feature: Dict[str, Any]
    area: float
    contains_point: bool
    centroid_dist_m: float


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


def fetch_metadata(dataset_id: str) -> Dict[str, Any]:
    url = f"{DATASF_BASE}/api/views/{dataset_id}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def choose_geom_field(metadata: Dict[str, Any]) -> Optional[str]:
    columns = metadata.get("columns", [])
    for col in columns:
        data_type = (col.get("dataTypeName") or "").lower()
        render_type = (col.get("renderTypeName") or "").lower()
        if data_type in {"multipolygon", "polygon", "location", "point"}:
            return col.get("fieldName")
        if render_type in {"geo_shape", "geo_point"}:
            return col.get("fieldName")
    return None


def query_features(dataset_id: str, geom_field: str, lat: float, lon: float, radius_m: float) -> Dict[str, Any]:
    endpoint = f"{DATASF_BASE}/resource/{dataset_id}.geojson"

    def do_query(lat_val: float, lon_val: float) -> Dict[str, Any]:
        where = f"within_circle({geom_field}, {lat_val}, {lon_val}, {radius_m})"
        params = {
            "$limit": 5000,
            "$where": where,
        }
        resp = requests.get(endpoint, params=params, timeout=60)
        resp.raise_for_status()
        return resp.json()

    data = do_query(lat, lon)
    if data.get("features"):
        return data

    # Try swapped lat/lon if nothing came back.
    data_swapped = do_query(lon, lat)
    if data_swapped.get("features"):
        return data_swapped

    return data


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def select_candidate(features: List[Dict[str, Any]], lat: float, lon: float) -> Tuple[FeatureCandidate, List[FeatureCandidate]]:
    point = Point(lon, lat)
    candidates: List[FeatureCandidate] = []
    for feat in features:
        geom = feat.get("geometry")
        if not geom:
            continue
        poly = shape(geom)
        contains = poly.contains(point)
        centroid = poly.centroid
        dist = haversine_m(lat, lon, centroid.y, centroid.x)
        candidates.append(
            FeatureCandidate(
                feature=feat,
                area=poly.area,
                contains_point=contains,
                centroid_dist_m=dist,
            )
        )

    if not candidates:
        raise ValueError("No valid geometry features returned.")

    containing = [c for c in candidates if c.contains_point]
    if containing:
        containing.sort(key=lambda c: (-c.area, c.centroid_dist_m))
        return containing[0], candidates

    candidates.sort(key=lambda c: (c.centroid_dist_m, -c.area))
    return candidates[0], candidates


def build_output_feature(feature: Dict[str, Any], name: str, source: str) -> Dict[str, Any]:
    props = feature.get("properties", {}).copy()
    props["selected_name"] = name
    props["selected_source"] = source
    return {
        "type": "Feature",
        "geometry": feature.get("geometry"),
        "properties": props,
    }


def write_geojson(path: Path, feature: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "type": "FeatureCollection",
        "features": [feature],
    }
    path.write_text(json.dumps(payload, indent=2))


def write_candidates(path: Path, candidates: List[FeatureCandidate]) -> None:
    features = []
    for idx, cand in enumerate(candidates, start=1):
        feat = cand.feature.copy()
        props = feat.get("properties", {}).copy()
        props["candidate_rank"] = idx
        props["candidate_area"] = cand.area
        props["candidate_contains_point"] = cand.contains_point
        props["candidate_centroid_distance_m"] = cand.centroid_dist_m
        feat["properties"] = props
        features.append(feat)
    payload = {"type": "FeatureCollection", "features": features}
    path.write_text(json.dumps(payload, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch a single building footprint near a lat/lon.")
    parser.add_argument("--name", required=True, help="Building name (for output labeling).")
    parser.add_argument("--lat", type=float, required=True, help="Latitude in WGS84.")
    parser.add_argument("--lon", type=float, required=True, help="Longitude in WGS84.")
    parser.add_argument("--radius-m", type=float, default=200.0, help="Search radius in meters.")
    parser.add_argument("--dataset-id", default=DATASET_ID_DEFAULT, help="DataSF dataset id.")
    parser.add_argument("--out", default=None, help="Output GeoJSON path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    slug = slugify(args.name)
    out_path = Path(args.out) if args.out else Path("data/footprints") / f"{slug}.geojson"
    candidates_path = out_path.with_name(f"{slug}_candidates.geojson")

    metadata = fetch_metadata(args.dataset_id)
    geom_field = choose_geom_field(metadata) or "the_geom"

    data = query_features(args.dataset_id, geom_field, args.lat, args.lon, args.radius_m)
    features = data.get("features", [])
    if not features:
        print("No features returned. Try increasing --radius-m or verifying coordinates.")
        return 2

    selected, candidates = select_candidate(features, args.lat, args.lon)
    selected_feature = build_output_feature(selected.feature, args.name, f"datasf:{args.dataset_id}")
    write_geojson(out_path, selected_feature)
    write_candidates(candidates_path, candidates)

    print(f"Saved footprint: {out_path}")
    print(f"Saved candidates: {candidates_path}")
    print(f"Geometry field used: {geom_field}")
    print(
        "Selected footprint info: "
        f"contains_point={selected.contains_point}, "
        f"area={selected.area:.2f}, "
        f"centroid_distance_m={selected.centroid_dist_m:.1f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
