#!/usr/bin/env python3
"""Create a local HTML map overlaying LiDAR points and a footprint."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import List, Tuple

import laspy
import numpy as np
from shapely.geometry import shape, mapping
from shapely.ops import transform
import pyproj


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Overlay LiDAR points and a footprint on a map.")
    parser.add_argument("--laz", required=True, help="Input LAZ/LAS path.")
    parser.add_argument("--footprint", required=True, help="Footprint GeoJSON path.")
    parser.add_argument("--out", required=True, help="Output HTML path.")
    parser.add_argument("--sample", type=int, default=20000, help="Number of points to sample.")
    return parser.parse_args()


def sample_points(x: np.ndarray, y: np.ndarray, z: np.ndarray, count: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = len(x)
    if n <= count:
        return x, y, z
    idx = np.random.choice(n, size=count, replace=False)
    return x[idx], y[idx], z[idx]


def main() -> int:
    args = parse_args()
    laz_path = Path(args.laz)
    footprint_path = Path(args.footprint)
    out_path = Path(args.out)

    las = laspy.read(laz_path)
    x, y, z = las.x, las.y, las.z
    x, y, z = sample_points(x, y, z, args.sample)

    # Convert to WGS84 for Leaflet.
    to_wgs84 = pyproj.Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    lon, lat = to_wgs84.transform(x, y)

    # Load footprint and ensure WGS84.
    footprint = json.loads(footprint_path.read_text())
    feature = footprint.get("features", [])[0]
    geom = shape(feature["geometry"])

    # Compute map center from footprint centroid.
    centroid = geom.centroid
    center_lat = centroid.y
    center_lon = centroid.x

    points = [[float(lon[i]), float(lat[i]), float(z[i])] for i in range(len(lon))]
    min_z = float(np.min(z)) if len(z) else 0.0
    max_z = float(np.max(z)) if len(z) else 1.0

    # Compute combined bounds (points + footprint).
    if len(lat):
        min_lat = float(np.min(lat))
        max_lat = float(np.max(lat))
        min_lon = float(np.min(lon))
        max_lon = float(np.max(lon))
    else:
        min_lat = max_lat = center_lat
        min_lon = max_lon = center_lon
    f_min_lon, f_min_lat, f_max_lon, f_max_lat = geom.bounds
    min_lat = min(min_lat, f_min_lat)
    max_lat = max(max_lat, f_max_lat)
    min_lon = min(min_lon, f_min_lon)
    max_lon = max(max_lon, f_max_lon)

    html = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <title>LiDAR Overlay</title>
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <link rel=\"stylesheet\" href=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.css\" />
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; }}
    #map {{ height: 85vh; }}
    #panel {{ padding: 12px 16px; background: #f8f8f8; border-top: 1px solid #ddd; }}
    .label {{ font-weight: 600; margin-bottom: 6px; }}
    .small {{ font-size: 12px; color: #555; }}
  </style>
</head>
<body>
  <div id=\"map\"></div>
  <div id=\"panel\">
    <div class=\"label\">LiDAR Overlay</div>
    <div class=\"small\">Sampled points: {len(points)} | Z range: {min_z:.2f}–{max_z:.2f} m | Drag to pan, scroll to zoom</div>
  </div>

  <script src=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.js\"></script>
  <script>
    const points = {json.dumps(points)};
    const footprint = {json.dumps(footprint)};

    const map = L.map('map').setView([{center_lat}, {center_lon}], 18);
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 20,
      attribution: '&copy; OpenStreetMap contributors'
    }}).addTo(map);

    function colorForZ(z) {{
      const minZ = {min_z:.2f};
      const maxZ = {max_z:.2f};
      const t = Math.max(0, Math.min(1, (z - minZ) / (maxZ - minZ || 1)));
      const r = Math.round(50 + 205 * t);
      const g = Math.round(80 + 100 * (1 - t));
      const b = Math.round(200 - 180 * t);
      return `rgb(${{r}},${{g}},${{b}})`;
    }}

    const pointLayer = L.layerGroup();
    for (const [lon, lat, z] of points) {{
      L.circleMarker([lat, lon], {{
        radius: 2,
        color: colorForZ(z),
        weight: 1,
        opacity: 0.8,
        fillOpacity: 0.5
      }}).addTo(pointLayer);
    }}
    pointLayer.addTo(map);

    const footprintLayer = L.geoJSON(footprint, {{
      style: {{ color: '#16a34a', weight: 2, fillColor: '#22c55e', fillOpacity: 0.15 }}
    }}).addTo(map);

    const bounds = L.latLngBounds(
      [{min_lat}, {min_lon}],
      [{max_lat}, {max_lon}]
    );
    map.fitBounds(bounds.pad(0.35));
  </script>
</body>
</html>
"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html)
    print(f"Wrote LiDAR overlay map to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
