#!/usr/bin/env python3
"""Union multiple footprint GeoJSONs into a single MultiPolygon feature."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List

from shapely.geometry import shape, mapping
from shapely.ops import unary_union


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Union multiple footprint GeoJSONs.")
    parser.add_argument("--inputs", nargs="+", required=True, help="Input GeoJSON files.")
    parser.add_argument("--out", required=True, help="Output GeoJSON path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    geoms = []
    for path_str in args.inputs:
        path = Path(path_str)
        data = json.loads(path.read_text())
        feats = data.get("features", [])
        if not feats:
            continue
        geom = feats[0].get("geometry")
        if not geom:
            continue
        geoms.append(shape(geom))

    if not geoms:
        raise SystemExit("No geometries loaded from inputs.")

    merged = unary_union(geoms)
    feature = {
        "type": "Feature",
        "geometry": mapping(merged),
        "properties": {"source": "union"},
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}, indent=2))
    print(f"Wrote union footprint to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
