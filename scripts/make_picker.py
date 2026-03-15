#!/usr/bin/env python3
"""Create a local HTML map to pick a building footprint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an HTML map to select a footprint.")
    parser.add_argument("--candidates", required=True, help="GeoJSON candidates file.")
    parser.add_argument("--out", required=True, help="Output HTML path.")
    parser.add_argument("--center-lat", type=float, required=True, help="Map center latitude.")
    parser.add_argument("--center-lon", type=float, required=True, help="Map center longitude.")
    parser.add_argument("--zoom", type=int, default=18, help="Initial zoom level.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cand_path = Path(args.candidates)
    out_path = Path(args.out)
    data = json.loads(cand_path.read_text())

    # Add a stable index for display (1-based).
    for idx, feat in enumerate(data.get("features", []), start=1):
        props = feat.setdefault("properties", {})
        props.setdefault("candidate_index", idx)

    geojson_text = json.dumps(data)

    html = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <title>Footprint Picker</title>
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <link rel=\"stylesheet\" href=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.css\" />
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; }}
    #map {{ height: 70vh; }}
    #panel {{ padding: 12px 16px; background: #f8f8f8; border-top: 1px solid #ddd; }}
    .label {{ font-weight: 600; margin-bottom: 8px; }}
    .value {{ font-family: monospace; white-space: pre-wrap; }}
  </style>
</head>
<body>
  <div id=\"map\"></div>
  <div id=\"panel\">
    <div class=\"label\">Click a footprint to select it</div>
    <div class=\"value\" id=\"info\">None selected</div>
  </div>

  <script src=\"https://unpkg.com/leaflet@1.9.4/dist/leaflet.js\"></script>
  <script>
    const geojson = {geojson_text};
    const map = L.map('map').setView([{args.center_lat}, {args.center_lon}], {args.zoom});

    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 20,
      attribution: '&copy; OpenStreetMap contributors'
    }}).addTo(map);

    function style(feature) {{
      return {{ color: '#2563eb', weight: 2, fillColor: '#60a5fa', fillOpacity: 0.35 }};
    }}

    let selectedLayer = null;
    function onEachFeature(feature, layer) {{
      const props = feature.properties || {{}};
      const label = `#${{props.candidate_index || '?'}}`;
      layer.bindTooltip(label, {{ permanent: false }});
      layer.on('click', () => {{
        if (selectedLayer) {{ selectedLayer.setStyle({{ color: '#2563eb', fillColor: '#60a5fa', fillOpacity: 0.35 }}); }}
        selectedLayer = layer;
        layer.setStyle({{ color: '#16a34a', fillColor: '#22c55e', fillOpacity: 0.45 }});
        const info = {{
          candidate_index: props.candidate_index || null,
          osm_id: props.osm_id || null,
          name: props.name || null,
          addr_housenumber: props['addr:housenumber'] || null,
          addr_street: props['addr:street'] || null,
          area_m2: props.candidate_area_m2 || null
        }};
        document.getElementById('info').textContent = JSON.stringify(info, null, 2);
      }});
    }}

    const layer = L.geoJSON(geojson, {{ style, onEachFeature }}).addTo(map);
    map.fitBounds(layer.getBounds(), {{ padding: [20, 20] }});
  </script>
</body>
</html>
"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html)
    print(f"Wrote picker map to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
