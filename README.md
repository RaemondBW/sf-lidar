# SF LiDAR Building Profiles

Generate clean side-profile vector silhouettes of SF buildings from USGS LiDAR.

## What this uses
- **LiDAR**: `CA_SanFrancisco_1_B23` via USGS public EPT (Web Mercator / EPSG:3857).
- **Footprints**: DataSF Building Footprints dataset `ynuv-fyni`. (Note: derived from a 2010 building model; newer buildings might be missing.)

## Quick start (Salesforce Tower)
Salesforce Tower approximate coordinates: `37.78978, -122.39692`.

1. Install dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Install PDAL (needed for EPT streaming)
- macOS (Homebrew): `brew install pdal`
- Conda: `conda install -c conda-forge pdal`

3. Fetch the building footprint
```bash
python scripts/fetch_footprint.py \
  --name "Salesforce Tower" \
  --lat 37.78978 \
  --lon -122.39692 \
  --radius-m 200
```
This writes:
- `data/footprints/salesforce_tower.geojson`
- `data/footprints/salesforce_tower_candidates.geojson` (all nearby candidates, ranked)

4. Clip the LiDAR to the footprint
```bash
python scripts/fetch_lidar_clip.py \
  --footprint data/footprints/salesforce_tower.geojson \
  --out data/lidar/salesforce_tower.laz
```

### If the DataSF footprint is wrong or missing (newer buildings)
Use OpenStreetMap footprints instead:
```bash
python scripts/fetch_footprint_osm.py \
  --name "Salesforce Tower" \
  --lat 37.78978 \
  --lon -122.39692 \
  --radius-m 200
```
Then re-run the clip with the OSM footprint:
```bash
python scripts/fetch_lidar_clip.py \
  --footprint data/footprints/salesforce_tower_osm.geojson \
  --out data/lidar/salesforce_tower.laz
```

### If the chosen footprint is still wrong: pick it on a map
1. Build the local picker map (uses the OSM candidates file):
```bash
python scripts/make_picker.py \
  --candidates data/footprints/salesforce_tower_osm_candidates.geojson \
  --out output/footprint_picker.html \
  --center-lat 37.78978 \
  --center-lon -122.39692 \
  --zoom 18
```
2. Open `output/footprint_picker.html`, click the correct building, and note the `candidate_index` or `osm_id`.
3. Write the selected footprint file:
```bash
python scripts/select_candidate.py \
  --candidates data/footprints/salesforce_tower_osm_candidates.geojson \
  --index 3 \
  --out data/footprints/salesforce_tower_selected.geojson
```
4. Re-run the LiDAR clip using the selected footprint:
```bash
python scripts/fetch_lidar_clip.py \
  --footprint data/footprints/salesforce_tower_selected.geojson \
  --out data/lidar/salesforce_tower.laz
```

5. Generate multiple profile angles
```bash
python scripts/make_profiles.py \
  --input data/lidar/salesforce_tower.laz \
  --out-dir output/salesforce_tower \
  --angles 12 \
  --bin-size 0.5
```
Open `output/salesforce_tower/index.html` to browse the angle previews.

### Point-cloud profile images (PNG)
If you prefer point clouds instead of SVG silhouettes:
```bash
python scripts/make_pointcloud_profiles.py \
  --input data/lidar/salesforce_tower.laz \
  --out-dir output/salesforce_tower \
  --angles 12
```
Open `output/salesforce_tower/index_points.html` to browse the point-cloud previews.
By default the PNG background is transparent; use `--bg white` if you want a solid fill.
To keep profiles to real scale across buildings, use a consistent pixels-per-meter:
```bash
python scripts/make_pointcloud_profiles.py \
  --input data/lidar/salesforce_tower.laz \
  --out-dir output/salesforce_tower \
  --angles 12 \
  --pixels-per-meter 5
```

## Picking a specific angle later
Once you know the best angle, re-run with explicit azimuths:
```bash
python scripts/make_profiles.py \
  --input data/lidar/salesforce_tower.laz \
  --out-dir output/salesforce_tower \
  --azimuths 240
```

## Notes
- The USGS EPT for `CA_SanFrancisco_1_B23` is in EPSG:3857, so footprints are reprojected before clipping.
- If the DataSF footprint doesn’t include a newer building, let me know and we can switch to OpenStreetMap footprints.
