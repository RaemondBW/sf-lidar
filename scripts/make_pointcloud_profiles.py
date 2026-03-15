#!/usr/bin/env python3
"""Render point-cloud profile images for multiple azimuths."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import List, Sequence

import laspy
import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render point-cloud profile images.")
    parser.add_argument("--input", required=True, help="Input LAZ/LAS path.")
    parser.add_argument("--out-dir", required=True, help="Output directory for PNGs.")
    parser.add_argument("--angles", type=int, default=12, help="Number of evenly spaced angles.")
    parser.add_argument("--azimuths", default=None, help="Comma-separated azimuths in degrees.")
    parser.add_argument("--min-height", type=float, default=2.0, help="Drop points below this height above min-z.")
    parser.add_argument("--sample", type=int, default=400000, help="Max points to sample per view.")
    parser.add_argument("--width", type=int, default=1600, help="Image width in px.")
    parser.add_argument("--height", type=int, default=800, help="Image height in px.")
    parser.add_argument("--point-size", type=float, default=0.3, help="Scatter point size.")
    parser.add_argument("--alpha", type=float, default=0.4, help="Point alpha.")
    parser.add_argument("--bg", default="transparent", help="Background color (use 'transparent' for alpha).")
    parser.add_argument("--aspect", default="equal", choices=["equal", "auto"], help="Aspect ratio mode.")
    parser.add_argument("--z-exaggeration", type=float, default=1.0, help="Vertical exaggeration factor.")
    parser.add_argument("--pixels-per-meter", type=float, default=0.0, help="Set scale in pixels per meter (0 disables).")
    parser.add_argument("--pad-m", type=float, default=0.0, help="Padding to add around extents in meters.")
    return parser.parse_args()


def parse_azimuths(raw: str | None, count: int) -> List[int]:
    if raw:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        return [int(float(p)) % 360 for p in parts]
    if count < 1:
        return [0]
    step = 360 / count
    return [int(round(step * i)) % 360 for i in range(count)]


def project(points_xy: np.ndarray, z: np.ndarray, azimuth_deg: float, min_height: float) -> tuple[np.ndarray, np.ndarray]:
    theta = math.radians(azimuth_deg)
    view = np.array([math.sin(theta), math.cos(theta)])  # 0 deg = north
    screen_x = np.array([-view[1], view[0]])

    centered = points_xy - points_xy.mean(axis=0)
    x_proj = centered @ screen_x

    z_min = float(np.min(z))
    z_rel = z - z_min

    if min_height > 0:
        mask = z_rel >= min_height
        x_proj = x_proj[mask]
        z_rel = z_rel[mask]

    return x_proj, z_rel


def sample_arrays(x: np.ndarray, y: np.ndarray, count: int) -> tuple[np.ndarray, np.ndarray]:
    if len(x) <= count:
        return x, y
    idx = np.random.choice(len(x), size=count, replace=False)
    return x[idx], y[idx]


def render_png(
    path: Path,
    x: np.ndarray,
    y: np.ndarray,
    x_min: float,
    x_max: float,
    y_max: float,
    width: int,
    height: int,
    point_size: float,
    alpha: float,
    bg: str,
    aspect: str,
    tight: bool,
) -> None:
    if len(x) == 0:
        path.write_text("")
        return
    dpi = 100
    fig = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi)
    ax = fig.add_subplot(111)
    transparent = bg.lower() == "transparent"
    if transparent:
        ax.set_facecolor((0, 0, 0, 0))
        fig.patch.set_facecolor((0, 0, 0, 0))
    else:
        ax.set_facecolor(bg)
        fig.patch.set_facecolor(bg)

    ax.scatter(x, y, s=point_size, c="black", alpha=alpha, linewidths=0)
    ax.set_aspect("equal" if aspect == "equal" else "auto", adjustable="box")
    ax.axis("off")
    ax.set_position([0, 0, 1, 1])

    # Tight bounds
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(0.0, y_max)
    ax.margins(0)

    if tight:
        fig.savefig(path, bbox_inches="tight", pad_inches=0, transparent=transparent)
    else:
        fig.savefig(path, pad_inches=0, transparent=transparent)
    plt.close(fig)


def write_index(out_dir: Path, entries: Sequence[dict]) -> None:
    cards = []
    for entry in entries:
        cards.append(
            f"<div class=\"card\"><div class=\"label\">{entry['label']}</div><img src=\"{entry['file']}\" /></div>"
        )
    html = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <title>Point Cloud Profiles</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; background: #f8f8f8; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }}
    .card {{ background: white; padding: 12px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); }}
    .card img {{ width: 100%; height: auto; }}
    .label {{ font-weight: 600; margin-bottom: 8px; }}
  </style>
</head>
<body>
  <h1>Point Cloud Profiles</h1>
  <div class=\"grid\">
    {''.join(cards)}
  </div>
</body>
</html>
"""
    (out_dir / "index_points.html").write_text(html)


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    las = laspy.read(args.input)
    x = las.x
    y = las.y
    z = las.z

    points_xy = np.column_stack([x, y])
    azimuths = parse_azimuths(args.azimuths, args.angles)

    entries = []
    metadata = []

    for az in azimuths:
        x_proj, z_rel = project(points_xy, z, az, args.min_height)
        if args.z_exaggeration != 1.0:
            z_rel = z_rel * args.z_exaggeration
        if len(x_proj) == 0 or len(z_rel) == 0:
            continue

        x_min = float(np.min(x_proj)) - args.pad_m
        x_max = float(np.max(x_proj)) + args.pad_m
        y_max = float(np.max(z_rel)) + args.pad_m
        x_range = max(1e-6, x_max - x_min)
        y_range = max(1e-6, y_max)

        width = args.width
        height = args.height
        tight = True
        if args.pixels_per_meter and args.pixels_per_meter > 0:
            width = max(10, int(math.ceil(x_range * args.pixels_per_meter)))
            height = max(10, int(math.ceil(y_range * args.pixels_per_meter)))
            tight = False

        x_proj, z_rel = sample_arrays(x_proj, z_rel, args.sample)
        filename = f"pc_profile_az{az:03d}.png"
        render_png(
            out_dir / filename,
            x_proj,
            z_rel,
            x_min,
            x_max,
            y_max,
            width,
            height,
            args.point_size,
            args.alpha,
            args.bg,
            args.aspect,
            tight,
        )
        entries.append({"label": f"Azimuth {az}°", "file": filename})
        metadata.append({"azimuth": az, "file": filename})

    write_index(out_dir, entries)
    (out_dir / "profiles_points.json").write_text(json.dumps(metadata, indent=2))
    print(f"Wrote {len(azimuths)} point-cloud profiles to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
