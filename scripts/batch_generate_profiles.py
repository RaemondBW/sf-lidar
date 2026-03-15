#!/usr/bin/env python3
"""Batch-generate LiDAR point-cloud PNGs and SVG profiles for a list of buildings."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List


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


def run(cmd: List[str], timeout: int = 300) -> Dict[str, object]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "cmd": cmd,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except Exception as exc:
        return {"cmd": cmd, "returncode": -1, "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch-generate profiles for buildings.")
    parser.add_argument("--list", required=True, help="JSON list of buildings with name/lat/lon.")
    parser.add_argument("--out-log", default="output/batch_log.json", help="Output log path.")
    parser.add_argument("--radius-m", type=float, default=250.0, help="OSM search radius.")
    parser.add_argument("--sleep", type=float, default=1.0, help="Sleep seconds between network calls.")
    parser.add_argument("--skip", default="", help="Comma-separated building names to skip.")
    parser.add_argument("--pixels-per-meter", type=float, default=5.0, help="PNG scale in pixels per meter.")
    return parser.parse_args().__dict__


def load_list(path: Path) -> List[Dict[str, object]]:
    data = json.loads(path.read_text())
    if isinstance(data, dict) and "buildings" in data:
        return data["buildings"]
    return data


def main_wrapper() -> int:
    args = main()
    list_path = Path(args["list"])
    out_log = Path(args["out_log"])
    out_log.parent.mkdir(parents=True, exist_ok=True)

    skip_names = {name.strip().lower() for name in args["skip"].split(",") if name.strip()}

    buildings = load_list(list_path)
    log: Dict[str, object] = {"buildings": []}

    py = sys.executable
    for item in buildings:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        if name.lower() in skip_names:
            log["buildings"].append({"name": name, "status": "skipped"})
            continue

        rank = item.get("rank")
        slug = slugify(name)
        if isinstance(rank, int):
            slug = f"r{rank:02d}_{slug}"

        lat = float(item["lat"])
        lon = float(item["lon"])

        footprint_path = Path("data/footprints") / f"{slug}_osm.geojson"
        lidar_path = Path("data/lidar") / f"{slug}.laz"
        out_dir = Path("output") / slug

        entry = {"name": name, "rank": rank, "slug": slug, "steps": []}

        # Fetch footprint
        cmd = [
            py,
            "scripts/fetch_footprint_osm.py",
            "--name",
            name,
            "--lat",
            f"{lat}",
            "--lon",
            f"{lon}",
            "--radius-m",
            f"{args['radius_m']}",
            "--out",
            str(footprint_path),
        ]
        entry["steps"].append(run(cmd, timeout=120))
        time.sleep(args["sleep"])

        # Clip LiDAR
        cmd = [
            py,
            "scripts/fetch_lidar_clip.py",
            "--footprint",
            str(footprint_path),
            "--out",
            str(lidar_path),
        ]
        entry["steps"].append(run(cmd, timeout=300))
        time.sleep(args["sleep"])

        # SVG profiles
        cmd = [
            py,
            "scripts/make_profiles.py",
            "--input",
            str(lidar_path),
            "--out-dir",
            str(out_dir),
            "--angles",
            "12",
            "--mode",
            "occupancy",
            "--bin-size",
            "0.25",
            "--z-bin-size",
            "0.5",
            "--min-occupancy",
            "3",
            "--neighbor-window",
            "3",
            "--neighbor-min",
            "4",
            "--min-bin-count",
            "10",
            "--keep-largest-run",
            "--smooth",
            "1",
            "--pad-m",
            "0",
            "--auto-width",
            "--use-lower-envelope",
        ]
        entry["steps"].append(run(cmd, timeout=300))

        # Point-cloud profiles (transparent background by default)
        cmd = [
            py,
            "scripts/make_pointcloud_profiles.py",
            "--input",
            str(lidar_path),
            "--out-dir",
            str(out_dir),
            "--angles",
            "12",
            "--pixels-per-meter",
            f"{args['pixels_per_meter']}",
        ]
        entry["steps"].append(run(cmd, timeout=300))

        entry["status"] = "done"
        log["buildings"].append(entry)

    out_log.write_text(json.dumps(log, indent=2))
    print(f"Wrote log to {out_log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_wrapper())
