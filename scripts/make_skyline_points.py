#!/usr/bin/env python3
"""Render a single skyline point-cloud PNG from a given view direction."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Tuple

import laspy
import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from shapely.geometry import shape
from shapely.ops import transform
import pyproj


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render skyline point-cloud PNG.")
    parser.add_argument("--input", required=True, help="Input LAZ/LAS path.")
    parser.add_argument("--center-footprint", required=True, help="GeoJSON footprint for centering.")
    parser.add_argument("--azimuth", type=float, default=240.0, help="View azimuth (deg, 0=north).")
    parser.add_argument("--min-height", type=float, default=2.0, help="Drop points below this height above min-z.")
    parser.add_argument("--sample", type=int, default=1500000, help="Max points to sample.")
    parser.add_argument("--pixels-per-meter", type=float, default=5.0, help="Scale in pixels per meter.")
    parser.add_argument("--pad-m", type=float, default=0.0, help="Padding around extents in meters.")
    parser.add_argument("--point-size", type=float, default=0.3, help="Scatter point size.")
    parser.add_argument("--alpha", type=float, default=0.4, help="Point alpha.")
    parser.add_argument("--out", required=True, help="Output PNG path.")
    return parser.parse_args()


def sample_arrays(x: np.ndarray, y: np.ndarray, count: int) -> Tuple[np.ndarray, np.ndarray]:
    if len(x) <= count:
        return x, y
    idx = np.random.choice(len(x), size=count, replace=False)
    return x[idx], y[idx]


def main() -> int:
    args = parse_args()

    las = laspy.read(args.input)
    x = las.x
    y = las.y
    z = las.z

    # Center on the Ferry Building footprint centroid (in EPSG:3857).
    footprint = json.loads(Path(args.center_footprint).read_text())
    feature = footprint.get("features", [])[0]
    geom = shape(feature["geometry"])
    to_3857 = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    geom_3857 = transform(to_3857.transform, geom)
    center = geom_3857.centroid

    points_xy = np.column_stack([x - center.x, y - center.y])

    # Project to view direction.
    theta = math.radians(args.azimuth)
    view = np.array([math.sin(theta), math.cos(theta)])
    screen_x = np.array([-view[1], view[0]])
    x_proj = points_xy @ screen_x

    z_min = float(np.min(z))
    z_rel = z - z_min

    if args.min_height > 0:
        mask = z_rel >= args.min_height
        x_proj = x_proj[mask]
        z_rel = z_rel[mask]

    if len(x_proj) == 0:
        raise SystemExit("No points after filtering.")

    x_min = float(np.min(x_proj)) - args.pad_m
    x_max = float(np.max(x_proj)) + args.pad_m
    y_max = float(np.max(z_rel)) + args.pad_m

    x_range = max(1e-6, x_max - x_min)
    y_range = max(1e-6, y_max)

    width = max(10, int(math.ceil(x_range * args.pixels_per_meter)))
    height = max(10, int(math.ceil(y_range * args.pixels_per_meter)))

    x_proj, z_rel = sample_arrays(x_proj, z_rel, args.sample)

    dpi = 100
    fig = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi)
    ax = fig.add_subplot(111)
    ax.set_facecolor((0, 0, 0, 0))
    fig.patch.set_facecolor((0, 0, 0, 0))

    ax.scatter(x_proj, z_rel, s=args.point_size, c="black", alpha=args.alpha, linewidths=0)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")
    ax.set_position([0, 0, 1, 1])

    ax.set_xlim(x_min, x_max)
    ax.set_ylim(0.0, y_max)
    ax.margins(0)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, pad_inches=0, transparent=True)
    plt.close(fig)

    print(f"Wrote skyline PNG to {out_path} ({width}x{height}px)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
