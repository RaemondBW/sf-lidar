# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SF LiDAR Building Profiles: generates side-profile vector silhouettes (SVG) and point-cloud images (PNG) of San Francisco buildings from USGS LiDAR data (`CA_SanFrancisco_1_B23` EPT, EPSG:3857).

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
brew install pdal  # required for EPT streaming (fetch_lidar_clip.py)
```

## Pipeline

The workflow is a multi-step pipeline, each step is a standalone script in `scripts/`:

1. **Fetch footprint** — `fetch_footprint.py` (DataSF) or `fetch_footprint_osm.py` (OSM) or `fetch_footprint_osm_id.py` (OSM by ID). Queries building polygons near a lat/lon, writes best match + all candidates as GeoJSON to `data/footprints/`.
2. **Pick footprint** (optional) — `make_picker.py` generates an interactive HTML map; `select_candidate.py` extracts a specific candidate by index.
3. **Clip LiDAR** — `fetch_lidar_clip.py` streams USGS EPT point cloud, clips to footprint polygon, writes LAZ to `data/lidar/`. Uses PDAL subprocess. Footprints are reprojected from WGS84 to EPSG:3857 before clipping.
4. **Generate profiles** — `make_profiles.py` (SVG silhouettes) or `make_pointcloud_profiles.py` (PNG point clouds) from a LAZ file. Outputs to `output/<building>/` with an `index.html` preview page.
5. **Batch processing** — `batch_generate_profiles.py` runs the full pipeline for a JSON list of buildings.

Other utilities: `union_footprints.py`, `make_3d_viewer.py`, `make_lidar_overlay.py`, `make_skyline_points.py`, `extract_wiki_tallest.py`.

## Key Conventions

- All scripts use `argparse` and are run directly: `python scripts/<name>.py --flag value`
- Building names are slugified (lowercase, underscores) for file paths
- Coordinates are WGS84 lat/lon; LiDAR data is EPSG:3857 (Web Mercator)
- Footprint sources: DataSF dataset `ynuv-fyni` (primary, from 2010 model) or OpenStreetMap (for newer buildings)
- Data flows: `data/footprints/*.geojson` → `data/lidar/*.laz` → `output/<building>/`
- Batch input files are JSON arrays of building objects with `name`, `lat`, `lon` fields, stored in `data/`

## Dependencies

Python: requests, shapely, pyproj, numpy, laspy, lazrs, matplotlib. System: PDAL (brew/conda).
