#!/usr/bin/env python3
"""Fetch a building footprint from OpenStreetMap by OSM id."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from shapely.geometry import Polygon, MultiPolygon, mapping


OVERPASS_DEFAULT = "https://overpass-api.de/api/interpreter"
USER_AGENT = "lidar-sf/0.1 (local script)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch an OSM building footprint by id.")
    parser.add_argument("--osm-id", type=int, required=True, help="OSM id (way or relation).")
    parser.add_argument("--osm-type", choices=["way", "relation"], default="way", help="OSM element type.")
    parser.add_argument("--name", required=True, help="Building name for labeling.")
    parser.add_argument("--overpass", default=OVERPASS_DEFAULT, help="Overpass API endpoint.")
    parser.add_argument("--out", required=True, help="Output GeoJSON path.")
    return parser.parse_args()


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


def main() -> int:
    args = parse_args()
    query = f"""
[out:json][timeout:25];
{args.osm_type}({args.osm_id});
out geom;
""".strip()

    resp = requests.post(args.overpass, data=query.encode("utf-8"), headers={"User-Agent": USER_AGENT}, timeout=60)
    resp.raise_for_status()
    payload = resp.json()

    elements = payload.get("elements", [])
    if not elements:
        print("No elements returned from Overpass.")
        return 2

    element = elements[0]
    geom = geometry_from_element(element)
    if geom is None or geom.is_empty:
        print("No valid polygon geometry returned.")
        return 2

    props = element.get("tags", {}).copy()
    props.update({
        "osm_type": element.get("type"),
        "osm_id": element.get("id"),
        "selected_name": args.name,
        "selected_source": "osm:overpass",
    })

    feature = {
        "type": "Feature",
        "geometry": mapping(geom),
        "properties": props,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}, indent=2))
    print(f"Saved footprint: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
