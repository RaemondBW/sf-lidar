#!/usr/bin/env python3
"""Generate skyline silhouette SVGs from a LAZ/LAS clip."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import List, Sequence

import numpy as np
import laspy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate silhouette profiles from point clouds.")
    parser.add_argument("--input", required=True, help="Input LAZ/LAS path.")
    parser.add_argument("--out-dir", required=True, help="Output directory for SVGs.")
    parser.add_argument("--angles", type=int, default=12, help="Number of evenly spaced angles.")
    parser.add_argument(
        "--azimuths",
        default=None,
        help="Comma-separated azimuths in degrees (overrides --angles).",
    )
    parser.add_argument("--bin-size", type=float, default=0.5, help="Horizontal bin size in meters.")
    parser.add_argument("--mode", choices=["quantile", "occupancy"], default="quantile", help="Profile extraction mode.")
    parser.add_argument("--min-height", type=float, default=0.0, help="Drop points below this height above min-z.")
    parser.add_argument("--smooth", type=int, default=1, help="Smoothing window in bins (odd integer).")
    parser.add_argument("--quantile", type=float, default=1.0, help="Use this height quantile per bin (1.0=max).")
    parser.add_argument("--lower-quantile", type=float, default=0.0, help="Use this lower quantile for base (quantile mode).")
    parser.add_argument("--min-bin-count", type=int, default=5, help="Drop bins with fewer points than this.")
    parser.add_argument("--keep-largest-run", action="store_true", help="Keep only the largest contiguous run of bins.")
    parser.add_argument("--xy-grid", type=float, default=0.0, help="Pre-filter points by XY grid size (meters).")
    parser.add_argument("--min-cell-count", type=int, default=0, help="Min points per XY grid cell to keep.")
    parser.add_argument("--median-window", type=int, default=7, help="Window size for median/MAD spike capping (odd).")
    parser.add_argument("--spike-mad", type=float, default=0.0, help="Cap spikes above median + k*MAD (0 disables).")
    parser.add_argument("--z-bin-size", type=float, default=0.5, help="Vertical bin size (meters) for occupancy mode.")
    parser.add_argument("--min-occupancy", type=int, default=3, help="Min points per (x,z) cell for occupancy mode.")
    parser.add_argument("--neighbor-window", type=int, default=3, help="Neighborhood window size for occupancy cleanup.")
    parser.add_argument("--neighbor-min", type=int, default=4, help="Min occupied neighbors to keep a cell (occupancy mode).")
    parser.add_argument("--use-lower-envelope", action="store_true", help="Use a lower envelope instead of a flat baseline (occupancy mode).")
    parser.add_argument("--pad-m", type=float, default=0.0, help="Padding to add around silhouette in meters.")
    parser.add_argument("--auto-width", action="store_true", help="Auto-compute SVG width from height and aspect ratio.")
    parser.add_argument("--auto-height", action="store_true", help="Auto-compute SVG height from width and aspect ratio.")
    parser.add_argument("--width", type=int, default=1200, help="SVG width in px.")
    parser.add_argument("--height", type=int, default=600, help="SVG height in px.")
    return parser.parse_args()


def parse_azimuths(raw: str | None, count: int) -> List[int]:
    if raw:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        return [int(float(p)) % 360 for p in parts]
    if count < 1:
        return [0]
    step = 360 / count
    return [int(round(step * i)) % 360 for i in range(count)]


def smooth_series(values: np.ndarray, window: int) -> np.ndarray:
    if window <= 1:
        return values
    if window % 2 == 0:
        window += 1
    kernel = np.ones(window) / window
    padded = np.pad(values, (window // 2, window // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def rolling_median(values: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or len(values) == 0:
        return values.copy()
    if window % 2 == 0:
        window += 1
    pad = window // 2
    padded = np.pad(values, (pad, pad), mode="edge")
    med = np.empty_like(values)
    for i in range(len(values)):
        med[i] = np.median(padded[i : i + window])
    return med


def rolling_mad(values: np.ndarray, window: int, med: np.ndarray) -> np.ndarray:
    if window <= 1 or len(values) == 0:
        return np.zeros_like(values)
    if window % 2 == 0:
        window += 1
    pad = window // 2
    padded = np.pad(values, (pad, pad), mode="edge")
    mad = np.empty_like(values)
    for i in range(len(values)):
        slice_vals = padded[i : i + window]
        mad[i] = np.median(np.abs(slice_vals - med[i]))
    return mad


def silhouette_quantile(
    points_xy: np.ndarray,
    z: np.ndarray,
    azimuth_deg: float,
    bin_size: float,
    min_height: float,
    quantile: float,
    lower_quantile: float,
    min_bin_count: int,
    keep_largest_run: bool,
    median_window: int,
    spike_mad: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
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

    if x_proj.size == 0:
        return np.array([]), np.array([])

    x_min = float(np.min(x_proj))
    x_max = float(np.max(x_proj))
    bins = np.floor((x_proj - x_min) / bin_size).astype(int)
    num_bins = int(np.ceil((x_max - x_min) / bin_size)) + 1

    x_vals = x_min + (np.arange(num_bins) + 0.5) * bin_size

    # Compute per-bin height using a quantile to reduce outliers.
    order = np.argsort(bins)
    bins_sorted = bins[order]
    z_sorted = z_rel[order]

    max_z = np.full(num_bins, -np.inf)
    min_z = np.full(num_bins, np.inf)
    counts = np.zeros(num_bins, dtype=int)
    np.add.at(counts, bins, 1)
    if len(bins_sorted) > 0:
        start = 0
        while start < len(bins_sorted):
            end = start + 1
            while end < len(bins_sorted) and bins_sorted[end] == bins_sorted[start]:
                end += 1
            bin_idx = bins_sorted[start]
            z_slice = z_sorted[start:end]
            if 0 <= bin_idx < num_bins and len(z_slice) > 0:
                q = float(np.clip(quantile, 0.0, 1.0))
                if q >= 0.999:
                    max_z[bin_idx] = float(np.max(z_slice))
                else:
                    max_z[bin_idx] = float(np.quantile(z_slice, q))
                if lower_quantile and lower_quantile > 0:
                    ql = float(np.clip(lower_quantile, 0.0, 1.0))
                    if ql <= 0.001:
                        min_z[bin_idx] = float(np.min(z_slice))
                    else:
                        min_z[bin_idx] = float(np.quantile(z_slice, ql))
            start = end

    valid = np.isfinite(max_z)
    if min_bin_count > 1:
        valid &= counts >= min_bin_count

    if keep_largest_run and np.any(valid):
        # Keep only the largest contiguous run of valid bins to drop stray edge spikes.
        idx = np.where(valid)[0]
        runs = []
        run_start = idx[0]
        prev = idx[0]
        for i in idx[1:]:
            if i == prev + 1:
                prev = i
                continue
            runs.append((run_start, prev))
            run_start = i
            prev = i
        runs.append((run_start, prev))
        run = max(runs, key=lambda r: r[1] - r[0])
        mask = (np.arange(num_bins) >= run[0]) & (np.arange(num_bins) <= run[1])
        valid &= mask

    x_out = x_vals[valid]
    y_out = max_z[valid]
    y_low = None
    if lower_quantile and lower_quantile > 0:
        low_valid = valid & np.isfinite(min_z)
        if np.any(low_valid):
            y_low = min_z[low_valid]
            x_out = x_vals[low_valid]
            y_out = max_z[low_valid]

    if spike_mad and spike_mad > 0 and len(y_out) > 3:
        med = rolling_median(y_out, median_window)
        mad = rolling_mad(y_out, median_window, med)
        cap = med + spike_mad * mad
        # If MAD is zero, cap equals median; only cap upward spikes.
        y_out = np.minimum(y_out, cap)

    return x_out, y_out, y_low


def silhouette_occupancy(
    points_xy: np.ndarray,
    z: np.ndarray,
    azimuth_deg: float,
    bin_size: float,
    z_bin_size: float,
    min_height: float,
    min_occupancy: int,
    neighbor_window: int,
    neighbor_min: int,
    min_bin_count: int,
    keep_largest_run: bool,
    use_lower_envelope: bool,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
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

    if x_proj.size == 0:
        return np.array([]), np.array([])

    x_min = float(np.min(x_proj))
    x_max = float(np.max(x_proj))
    x_bins = np.floor((x_proj - x_min) / bin_size).astype(int)
    z_bins = np.floor(z_rel / z_bin_size).astype(int)

    num_x = int(np.ceil((x_max - x_min) / bin_size)) + 1
    num_z = int(np.ceil((float(np.max(z_rel))) / z_bin_size)) + 1

    grid = np.zeros((num_x, num_z), dtype=int)
    np.add.at(grid, (x_bins, z_bins), 1)
    occupied = grid >= max(1, min_occupancy)

    # Remove isolated cells by neighbor count.
    window = max(1, neighbor_window)
    if window % 2 == 0:
        window += 1
    pad = window // 2
    if window > 1:
        padded = np.pad(occupied.astype(int), ((pad, pad), (pad, pad)), mode="constant")
        counts = np.zeros_like(occupied, dtype=int)
        for dx in range(window):
            for dz in range(window):
                counts += padded[dx : dx + num_x, dz : dz + num_z]
        occupied = occupied & (counts >= neighbor_min)

    # Determine topmost occupied z per x.
    max_z_bin = np.full(num_x, -1, dtype=int)
    min_z_bin = np.full(num_x, -1, dtype=int)
    for xi in range(num_x):
        z_idx = np.where(occupied[xi])[0]
        if z_idx.size > 0:
            max_z_bin[xi] = int(z_idx.max())
            min_z_bin[xi] = int(z_idx.min())

    x_vals = x_min + (np.arange(num_x) + 0.5) * bin_size
    valid = max_z_bin >= 0

    if min_bin_count > 1:
        counts_x = grid.sum(axis=1)
        valid &= counts_x >= min_bin_count

    if keep_largest_run and np.any(valid):
        idx = np.where(valid)[0]
        runs = []
        run_start = idx[0]
        prev = idx[0]
        for i in idx[1:]:
            if i == prev + 1:
                prev = i
                continue
            runs.append((run_start, prev))
            run_start = i
            prev = i
        runs.append((run_start, prev))
        run = max(runs, key=lambda r: r[1] - r[0])
        mask = (np.arange(num_x) >= run[0]) & (np.arange(num_x) <= run[1])
        valid &= mask

    y_vals = (max_z_bin.astype(float) + 0.5) * z_bin_size
    y_low = None
    if use_lower_envelope:
        low_valid = valid & (min_z_bin >= 0)
        if np.any(low_valid):
            y_low = (min_z_bin.astype(float) + 0.5) * z_bin_size
            return x_vals[low_valid], y_vals[low_valid], y_low[low_valid]

    return x_vals[valid], y_vals[valid], y_low


def write_svg(
    path: Path,
    x: np.ndarray,
    y: np.ndarray,
    y_low: np.ndarray | None,
    width: int,
    height: int,
    title: str,
    pad_m: float,
    auto_width: bool,
    auto_height: bool,
) -> None:
    if x.size == 0:
        path.write_text("<!-- empty silhouette -->")
        return

    x_min, x_max = float(x.min()), float(x.max())
    if y_low is not None and len(y_low) > 0:
        y_min = float(np.min(y_low))
    else:
        y_min = 0.0
    y_max = float(y.max())

    pad_x = max(0.0, pad_m)
    pad_y = max(0.0, pad_m)

    x_min -= pad_x
    x_max += pad_x
    y_max += pad_y

    x_range = max(1e-6, x_max - x_min)
    y_range = max(1e-6, y_max - y_min)

    if auto_width:
        width = max(10, int(round(height * (x_range / y_range))))
    if auto_height:
        height = max(10, int(round(width * (y_range / x_range))))

    scale_x = width / x_range
    scale_y = height / y_range
    scale = min(scale_x, scale_y)

    def to_svg(px: float, py: float) -> tuple[float, float]:
        sx = (px - x_min) * scale
        sy = height - (py - y_min) * scale
        return sx, sy

    points = [to_svg(xi, yi) for xi, yi in zip(x, y)]
    if y_low is not None and len(y_low) > 0:
        base_left = to_svg(x[0], y_low[0])
        base_right = to_svg(x[-1], y_low[-1])
    else:
        base_left = to_svg(x[0], 0.0)
        base_right = to_svg(x[-1], 0.0)

    path_d = [f"M {points[0][0]:.2f} {points[0][1]:.2f}"]
    for px, py in points[1:]:
        path_d.append(f"L {px:.2f} {py:.2f}")
    if y_low is not None and len(y_low) > 0:
        low_points = [to_svg(xi, yi) for xi, yi in zip(x, y_low)]
        for px, py in reversed(low_points):
            path_d.append(f"L {px:.2f} {py:.2f}")
    else:
        path_d.append(f"L {base_right[0]:.2f} {base_right[1]:.2f}")
        path_d.append(f"L {base_left[0]:.2f} {base_left[1]:.2f}")
    path_d.append("Z")

    svg = f"""<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{width}\" height=\"{height}\" viewBox=\"0 0 {width} {height}\">
  <title>{title}</title>
  <rect width=\"100%\" height=\"100%\" fill=\"white\"/>
  <path d=\"{' '.join(path_d)}\" fill=\"black\" stroke=\"black\" stroke-width=\"1\" />
</svg>
"""
    path.write_text(svg)


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
  <title>Profile Preview</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; background: #f8f8f8; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }}
    .card {{ background: white; padding: 12px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.15); }}
    .card img {{ width: 100%; height: auto; }}
    .label {{ font-weight: 600; margin-bottom: 8px; }}
  </style>
</head>
<body>
  <h1>Profile Preview</h1>
  <div class=\"grid\">
    {''.join(cards)}
  </div>
</body>
</html>
"""
    (out_dir / "index.html").write_text(html)


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    las = laspy.read(args.input)
    x = las.x
    y = las.y
    z = las.z

    if args.xy_grid and args.xy_grid > 0 and args.min_cell_count and args.min_cell_count > 1:
        minx = float(np.min(x))
        miny = float(np.min(y))
        ix = np.floor((x - minx) / args.xy_grid).astype(np.int64)
        iy = np.floor((y - miny) / args.xy_grid).astype(np.int64)
        key = (ix << 32) | (iy & 0xFFFFFFFF)
        uniq, counts = np.unique(key, return_counts=True)
        count_map = dict(zip(uniq.tolist(), counts.tolist()))
        keep_mask = np.array([count_map[k] >= args.min_cell_count for k in key], dtype=bool)
        x = x[keep_mask]
        y = y[keep_mask]
        z = z[keep_mask]

    points_xy = np.column_stack([x, y])
    azimuths = parse_azimuths(args.azimuths, args.angles)

    entries = []
    metadata = []

    for az in azimuths:
        if args.mode == "occupancy":
            x_vals, y_vals, y_low = silhouette_occupancy(
                points_xy,
                z,
                az,
                args.bin_size,
                args.z_bin_size,
                args.min_height,
                args.min_occupancy,
                args.neighbor_window,
                args.neighbor_min,
                args.min_bin_count,
                args.keep_largest_run,
                args.use_lower_envelope,
            )
        else:
            x_vals, y_vals, y_low = silhouette_quantile(
                points_xy,
                z,
                az,
                args.bin_size,
                args.min_height,
                args.quantile,
                args.lower_quantile,
                args.min_bin_count,
                args.keep_largest_run,
                args.median_window,
                args.spike_mad,
            )
        y_vals = smooth_series(y_vals, args.smooth)
        if y_low is not None:
            y_low = smooth_series(y_low, args.smooth)
            y_low = np.minimum(y_low, y_vals)
        filename = f"profile_az{az:03d}.svg"
        write_svg(
            out_dir / filename,
            x_vals,
            y_vals,
            y_low,
            args.width,
            args.height,
            f"Azimuth {az}°",
            args.pad_m,
            args.auto_width,
            args.auto_height,
        )
        entries.append({"label": f"Azimuth {az}°", "file": filename})
        metadata.append({"azimuth": az, "file": filename})

    write_index(out_dir, entries)
    (out_dir / "profiles.json").write_text(json.dumps(metadata, indent=2))

    print(f"Wrote {len(azimuths)} profiles to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
