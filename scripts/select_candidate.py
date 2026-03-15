#!/usr/bin/env python3
"""Select a single footprint from a candidates GeoJSON by index or OSM id."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select a single footprint candidate.")
    parser.add_argument("--candidates", required=True, help="GeoJSON candidates file.")
    parser.add_argument("--index", type=int, default=None, help="1-based candidate index.")
    parser.add_argument("--osm-id", type=int, default=None, help="OSM id to select.")
    parser.add_argument("--out", required=True, help="Output GeoJSON path for the selected footprint.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.index is None and args.osm_id is None:
        print("Provide --index or --osm-id.")
        return 2

    data = json.loads(Path(args.candidates).read_text())
    features: List[Dict[str, Any]] = data.get("features", [])
    if not features:
        print("No features in candidates file.")
        return 2

    selected = None
    if args.index is not None:
        idx = args.index - 1
        if idx < 0 or idx >= len(features):
            print("Index out of range.")
            return 2
        selected = features[idx]

    if selected is None and args.osm_id is not None:
        for feat in features:
            if feat.get("properties", {}).get("osm_id") == args.osm_id:
                selected = feat
                break

    if selected is None:
        print("No matching candidate found.")
        return 2

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"type": "FeatureCollection", "features": [selected]}, indent=2))
    print(f"Wrote selected footprint to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
