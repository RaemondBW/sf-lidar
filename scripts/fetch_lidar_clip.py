#!/usr/bin/env python3
"""Clip USGS EPT LiDAR to a building footprint and write a LAZ file."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any, Dict

from shapely.geometry import shape
from shapely.ops import transform
import pyproj


DEFAULT_EPT = "https://s3-us-west-2.amazonaws.com/usgs-lidar-public/CA_SanFrancisco_1_B23/ept.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clip EPT LiDAR to a footprint polygon.")
    parser.add_argument("--ept", default=DEFAULT_EPT, help="EPT URL.")
    parser.add_argument("--footprint", required=True, help="Footprint GeoJSON path.")
    parser.add_argument("--out", required=True, help="Output LAZ path.")
    parser.add_argument("--bounds-pad", type=float, default=5.0, help="Pad bounds (meters).")
    parser.add_argument("--buffer-m", type=float, default=0.0, help="Buffer the footprint by this many meters before cropping.")
    parser.add_argument("--decimation", type=int, default=1, help="Keep every Nth point.")
    parser.add_argument("--class", dest="class_filter", default=None, help="Optional class filter, e.g. 6 for buildings.")
    return parser.parse_args()


def load_footprint(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text())
    features = data.get("features", [])
    if not features:
        raise ValueError("Footprint GeoJSON has no features.")
    return features[0]


def build_pipeline(ept_url: str, polygon_wkt: str, bounds: str, out_path: Path, decimation: int, class_filter: str | None) -> Dict[str, Any]:
    pipeline = [
        {
            "type": "readers.ept",
            "filename": ept_url,
            "bounds": bounds,
        },
        {
            "type": "filters.crop",
            "polygon": polygon_wkt,
        },
    ]

    if class_filter:
        pipeline.append({"type": "filters.range", "limits": f"Classification[{class_filter}:{class_filter}]"})

    if decimation and decimation > 1:
        pipeline.append({"type": "filters.decimation", "step": decimation})

    pipeline.append(
        {
            "type": "writers.las",
            "filename": str(out_path),
            "compression": "laszip",
        }
    )

    return {"pipeline": pipeline}


def run_pipeline(pipeline: Dict[str, Any]) -> None:
    try:
        import pdal  # type: ignore

        pipeline_obj = pdal.Pipeline(json.dumps(pipeline))
        pipeline_obj.execute()
        return
    except ImportError:
        pass

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as temp_file:
        json.dump(pipeline, temp_file, indent=2)
        temp_path = temp_file.name

    import subprocess

    subprocess.check_call(["pdal", "pipeline", temp_path])


def main() -> int:
    args = parse_args()
    footprint_path = Path(args.footprint)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    feature = load_footprint(footprint_path)
    geom = shape(feature["geometry"])

    # EPT for CA_SanFrancisco_1_B23 is in EPSG:3857 (Web Mercator).
    transformer = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    geom_3857 = transform(transformer.transform, geom)
    if args.buffer_m and args.buffer_m > 0:
        geom_3857 = geom_3857.buffer(args.buffer_m)

    minx, miny, maxx, maxy = geom_3857.bounds
    pad = args.bounds_pad
    bounds = f"([{minx - pad},{maxx + pad}],[{miny - pad},{maxy + pad}])"
    polygon_wkt = geom_3857.wkt

    pipeline = build_pipeline(args.ept, polygon_wkt, bounds, out_path, args.decimation, args.class_filter)
    run_pipeline(pipeline)

    print(f"Saved LiDAR clip: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
