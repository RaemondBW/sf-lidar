#!/usr/bin/env python3
"""Render a skyline view from the bay looking down Market Street.

Loads a large-area LAZ clip, projects from a chosen viewpoint,
filters by depth and height, and renders via 2D histogram rasterization
with depth-based shading (nearer buildings are darker).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import laspy
import numpy as np
import pyproj

from shapely.geometry import shape
from shapely.ops import transform


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render skyline view from the bay.")
    parser.add_argument("--input", required=True, help="Input LAZ/LAS path.")
    parser.add_argument("--center-footprint", required=True, help="GeoJSON footprint for centering (e.g. Ferry Building).")
    parser.add_argument("--azimuth", type=float, default=245.0, help="View azimuth in degrees (0=north, 245≈down Market St).")
    parser.add_argument("--min-height", type=float, default=3.0, help="Drop points below this height above ground (meters).")
    parser.add_argument("--ground-z", type=float, default=None, help="Absolute z for ground plane. Default: 10th percentile of z.")
    parser.add_argument("--depth-min", type=float, default=-200.0, help="Min depth from center (negative = in front of center).")
    parser.add_argument("--depth-max", type=float, default=2000.0, help="Max depth from center (positive = behind center).")
    parser.add_argument("--x-min", type=float, default=None, help="Crop screen-space left bound (meters from center).")
    parser.add_argument("--x-max", type=float, default=None, help="Crop screen-space right bound (meters from center).")
    parser.add_argument("--width", type=int, default=4000, help="Output image width in pixels.")
    parser.add_argument("--height", type=int, default=1400, help="Output image height in pixels.")
    parser.add_argument("--gamma", type=float, default=0.45, help="Gamma curve for density mapping (lower = more contrast).")
    parser.add_argument("--depth-fade", type=float, default=0.6, help="How much to fade far points (0=no fade, 1=full fade).")
    parser.add_argument("--spread", type=int, default=0, help="Spread each point across NxN pixel kernel (0=auto based on scale).")
    parser.add_argument("--bg", default="white", help="Background color name or 'transparent'.")
    parser.add_argument("--out", required=True, help="Output PNG path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # Load center footprint and get its centroid in EPSG:3857.
    footprint = json.loads(Path(args.center_footprint).read_text())
    feature = footprint["features"][0]
    geom = shape(feature["geometry"])
    to_3857 = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    geom_3857 = transform(to_3857.transform, geom)
    center = geom_3857.centroid
    print(f"Center: {center.x:.1f}, {center.y:.1f} (EPSG:3857)")

    # Load point cloud.
    print("Reading LAZ...")
    las = laspy.read(args.input)
    x = np.array(las.x, dtype=np.float64)
    y = np.array(las.y, dtype=np.float64)
    z = np.array(las.z, dtype=np.float64)
    print(f"Loaded {len(x):,} points")

    # Center on footprint.
    x -= center.x
    y -= center.y

    # Establish ground plane and filter by height above it.
    if args.ground_z is not None:
        ground_z = args.ground_z
    else:
        ground_z = float(np.percentile(z, 10))
    z_rel = z - ground_z
    mask = z_rel >= args.min_height
    x, y, z_rel = x[mask], y[mask], z_rel[mask]
    print(f"Ground z={ground_z:.1f}m, after height filter (>={args.min_height}m): {len(x):,} points")

    # Set up projection axes.
    theta = math.radians(args.azimuth)
    view_dir = np.array([math.sin(theta), math.cos(theta)])
    screen_x_axis = np.array([-view_dir[1], view_dir[0]])

    points_xy = np.column_stack([x, y])
    proj_x = points_xy @ screen_x_axis
    proj_depth = points_xy @ view_dir

    # Filter by depth range.
    depth_mask = (proj_depth >= args.depth_min) & (proj_depth <= args.depth_max)
    proj_x = proj_x[depth_mask]
    z_rel = z_rel[depth_mask]
    proj_depth = proj_depth[depth_mask]
    print(f"After depth filter [{args.depth_min}, {args.depth_max}]: {len(proj_x):,} points")

    # Crop horizontal range.
    if args.x_min is not None or args.x_max is not None:
        x_lo = args.x_min if args.x_min is not None else float(np.min(proj_x))
        x_hi = args.x_max if args.x_max is not None else float(np.max(proj_x))
        x_mask = (proj_x >= x_lo) & (proj_x <= x_hi)
        proj_x = proj_x[x_mask]
        z_rel = z_rel[x_mask]
        proj_depth = proj_depth[x_mask]
        print(f"After x crop [{x_lo}, {x_hi}]: {len(proj_x):,} points")

    if len(proj_x) == 0:
        print("No points remaining after filtering.")
        return 1

    # Determine screen-space bounds (equal aspect ratio).
    sx_min, sx_max = float(np.min(proj_x)), float(np.max(proj_x))
    sz_max = float(np.max(z_rel))
    x_range = sx_max - sx_min
    z_range = sz_max

    # Fit to image: use width as the controlling dimension, compute height from aspect.
    scale = args.width / x_range
    eff_w = args.width
    eff_h = int(round(z_range * scale))
    # Add sky padding above the tallest point (20% of building height).
    sky_pad = max(int(eff_h * 0.15), 20)
    canvas_h = eff_h + sky_pad
    pad_left = 0
    pad_bottom = 0

    print(f"Scale: {scale:.2f} px/m, effective {eff_w}x{eff_h}, canvas {eff_w}x{canvas_h}")

    # --- Rasterize with depth-weighted accumulation ---
    # Map each point to a pixel.
    px = ((proj_x - sx_min) * scale).astype(np.int32)
    pz = (z_rel * scale).astype(np.int32)
    np.clip(px, 0, eff_w - 1, out=px)
    np.clip(pz, 0, eff_h - 1, out=pz)

    # Depth-based weight: near points contribute more darkness.
    d_min, d_max = float(np.min(proj_depth)), float(np.max(proj_depth))
    d_range = max(d_max - d_min, 1.0)
    depth_norm = (proj_depth - d_min) / d_range
    # Weight: near=1.0, far=(1-depth_fade).
    weights = 1.0 - args.depth_fade * depth_norm

    # Determine spread radius: auto-scale so each point covers ~1m².
    spread = args.spread
    if spread <= 0:
        spread = max(1, int(round(scale / 2.5)))
    print(f"Point spread kernel: {spread}x{spread}px")

    # Accumulate weighted density per pixel with spread kernel.
    density = np.zeros((eff_h, eff_w), dtype=np.float64)
    pz_flip = eff_h - 1 - pz
    if spread <= 1:
        np.add.at(density, (pz_flip, px), weights)
    else:
        half = spread // 2
        for dy in range(-half, half + 1):
            for dx in range(-half, half + 1):
                r2 = dy * dy + dx * dx
                if r2 > (half + 0.5) ** 2:
                    continue
                ry = np.clip(pz_flip + dy, 0, eff_h - 1)
                rx = np.clip(px + dx, 0, eff_w - 1)
                np.add.at(density, (ry, rx), weights)

    # Normalize using log-scale to compress the huge dynamic range
    # between dense ground returns and sparse building facades.
    mask_nz = density > 0
    if np.any(mask_nz):
        density[mask_nz] = np.log1p(density[mask_nz])
        cap = float(np.percentile(density[mask_nz], 98))
        density = np.clip(density / max(cap, 1e-6), 0.0, 1.0)
    density = np.power(density, args.gamma)

    # Build output image (white background, black buildings).
    # density=1 -> black, density=0 -> white.
    img = np.ones((canvas_h, eff_w, 4), dtype=np.float32)
    # Place the rasterized region.
    y_start = canvas_h - eff_h - pad_bottom
    x_start = pad_left
    y_end = y_start + eff_h
    x_end = x_start + eff_w

    bg_transparent = args.bg.lower() == "transparent"
    if bg_transparent:
        img[:, :, :3] = 0.0
        img[:, :, 3] = 0.0
        # Buildings: black with alpha = density.
        region = density
        img[y_start:y_end, x_start:x_end, 3] = region
    else:
        # White background, darken by density.
        for c in range(3):
            img[y_start:y_end, x_start:x_end, c] = 1.0 - density
        img[:, :, 3] = 1.0

    # Save using matplotlib's imsave.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.imsave(str(out_path), img)

    print(f"Wrote {out_path} ({eff_w}x{canvas_h}px, {len(proj_x):,} points rasterized)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
