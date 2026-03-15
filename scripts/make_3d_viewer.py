#!/usr/bin/env python3
"""Create a standalone 3D HTML viewer for LiDAR points + footprint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Tuple

import laspy
import numpy as np
from shapely.geometry import shape
from shapely.ops import transform
import pyproj


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a 3D HTML viewer for a LiDAR clip.")
    parser.add_argument("--laz", required=True, help="Input LAZ/LAS path.")
    parser.add_argument("--footprint", required=True, help="Footprint GeoJSON path.")
    parser.add_argument("--out", required=True, help="Output HTML path.")
    parser.add_argument("--sample", type=int, default=50000, help="Number of points to sample.")
    return parser.parse_args()


def sample_points(x: np.ndarray, y: np.ndarray, z: np.ndarray, count: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = len(x)
    if n <= count:
        return x, y, z
    idx = np.random.choice(n, size=count, replace=False)
    return x[idx], y[idx], z[idx]


def color_for_z(z_vals: np.ndarray) -> List[List[int]]:
    if len(z_vals) == 0:
        return []
    z_min = float(np.min(z_vals))
    z_max = float(np.max(z_vals))
    denom = z_max - z_min if z_max > z_min else 1.0
    colors = []
    for z in z_vals:
        t = float((z - z_min) / denom)
        r = int(50 + 205 * t)
        g = int(80 + 100 * (1 - t))
        b = int(200 - 180 * t)
        colors.append([r, g, b])
    return colors


def main() -> int:
    args = parse_args()
    laz_path = Path(args.laz)
    footprint_path = Path(args.footprint)
    out_path = Path(args.out)

    las = laspy.read(laz_path)
    x, y, z = las.x, las.y, las.z
    x, y, z = sample_points(x, y, z, args.sample)

    # Use EPSG:3857 coordinates (meters) and center them for local view.
    cx = float(np.mean(x)) if len(x) else 0.0
    cy = float(np.mean(y)) if len(y) else 0.0
    z_min = float(np.min(z)) if len(z) else 0.0

    points = []
    colors = color_for_z(z)
    for i in range(len(x)):
        points.append([float(x[i] - cx), float(y[i] - cy), float(z[i] - z_min)])

    # Load footprint, project to EPSG:3857, then center like points.
    footprint = json.loads(footprint_path.read_text())
    feature = footprint.get("features", [])[0]
    geom = shape(feature["geometry"])
    to_3857 = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    geom_3857 = transform(to_3857.transform, geom)

    footprint_coords = []
    if geom_3857.geom_type == "Polygon":
        rings = [list(geom_3857.exterior.coords)]
    else:
        rings = []
        for poly in geom_3857.geoms:
            rings.append(list(poly.exterior.coords))

    for ring in rings:
        footprint_coords.append([[float(px - cx), float(py - cy), 0.0] for px, py in ring])

    # Estimate view distance for zoom.
    extent = 0.0
    if len(points):
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        extent = max(max(xs) - min(xs), max(ys) - min(ys))
    extent = max(extent, 50.0)

    html = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <title>LiDAR 3D Viewer</title>
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; background: #0f172a; color: #e2e8f0; }}
    #deck-canvas {{ position: absolute; width: 100vw; height: 100vh; }}
    #panel {{ position: absolute; top: 12px; left: 12px; background: rgba(15, 23, 42, 0.8); padding: 10px 12px; border-radius: 8px; font-size: 12px; }}
    .label {{ font-weight: 600; margin-bottom: 4px; }}
  </style>
</head>
<body>
  <canvas id=\"deck-canvas\"></canvas>
  <div id=\"panel\">
    <div class=\"label\">3D LiDAR Viewer</div>
    <div>Drag to rotate, scroll to zoom, right-drag to pan</div>
    <div>Points: {len(points)} | Height range: {float(np.max(z) - z_min if len(z) else 0):.1f} m</div>
  </div>

  <script src=\"https://unpkg.com/deck.gl@8.9.33/dist.min.js\"></script>
  <script>
    const points = {json.dumps(points)};
    const colors = {json.dumps(colors)};
    const footprints = {json.dumps(footprint_coords)};

    const pointData = points.map((p, i) => ({{ position: p, color: colors[i] }}));

    const pointLayer = new deck.PointCloudLayer({{
      id: 'points',
      data: pointData,
      getPosition: d => d.position,
      getColor: d => d.color,
      pointSize: 1.8,
      coordinateSystem: deck.COORDINATE_SYSTEM.CARTESIAN
    }});

    const footprintLayer = new deck.PolygonLayer({{
      id: 'footprint',
      data: footprints.map(ring => ({{ polygon: ring }})),
      getPolygon: d => d.polygon,
      getFillColor: [34, 197, 94, 40],
      getLineColor: [34, 197, 94],
      lineWidthMinPixels: 2,
      coordinateSystem: deck.COORDINATE_SYSTEM.CARTESIAN
    }});

    const deckgl = new deck.Deck({{
      canvas: 'deck-canvas',
      views: new deck.OrbitView(),
      initialViewState: {{
        target: [0, 0, 40],
        rotationX: 60,
        rotationOrbit: 40,
        zoom: Math.log2(2000 / {extent:.1f}),
        minZoom: -2,
        maxZoom: 10
      }},
      controller: true,
      layers: [pointLayer, footprintLayer]
    }});
  </script>
</body>
</html>
"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html)
    print(f"Wrote 3D viewer to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
