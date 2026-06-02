# America's Byways KML For Google Earth

This repository provides a Google Earth KML overlay for **all 184 current FHWA America's Byways**.

It includes both:

- **All-American Roads**
- **National Scenic Byways**

The main file is:

[`americas_byways.kml`](./americas_byways.kml)

A small Google Earth Network Link file is also provided:

[`network_link.kml`](./network_link.kml)

## Use In Google Earth

### Option 1: Download And Open

Download `americas_byways.kml`, then open it in Google Earth Pro or another KML-compatible GIS viewer.

In Google Earth Pro:

1. Open Google Earth Pro.
2. Choose `File` -> `Open`.
3. Select `americas_byways.kml`.

### Option 2: Use As A Network Link

After this repository is published with GitHub Pages, the KML should be available at:

```text
https://dongfang-a.github.io/americas_byways/americas_byways.kml
```

In Google Earth Pro:

1. Choose `Add` -> `Network Link`.
2. Set the link URL to:

```text
https://dongfang-a.github.io/americas_byways/americas_byways.kml
```

3. Save the Network Link.

Using a Network Link makes it easier to refresh the map when the KML is updated.

You can also open:

```text
https://dongfang-a.github.io/americas_byways/network_link.kml
```

This small file points Google Earth to the main KML.

## Map Legend

- **All-American Roads**
  - Color: magenta `#ff00ff`
  - Line width: 5 pt

- **National Scenic Byways**
  - Color: green `#00ff00`
  - Line width: 2 pt

- **Star markers**
  - Start and end points for route parts.
  - If a route part is a loop, one star marks the shared start/end point.

If a Byway is both an All-American Road and a National Scenic Byway, it is styled as an All-American Road.

## How The KML Is Organized

- Byways are grouped under state folders.
- There are **44 state folders**.
- Each Byway appears under one state folder.
- Cross-state Byways are placed under one state, usually the state with the longest route geometry.
- Each Byway has one route item.
- Each route item uses KML `MultiGeometry` to hold its route lines.
- Start/end stars are included for continuous route parts.

Current structure:

- America's Byways: **184**
- Route Placemarks: **184**
- Route LineStrings: **19700**
- Endpoint star markers: **582**
- Missing geometry routes: **0**

## Files

- `americas_byways.kml`
  - Main Google Earth KML overlay.

- `network_link.kml`
  - Small Google Earth Network Link pointing to the hosted KML.

- `americas_byways_coverage_report.csv`
  - Coverage and metadata report.
  - Includes route source, assigned state, segment counts, endpoint marker counts, and geometry source.

- `fhwa_americas_byways_list.csv`
  - FHWA-derived list of all 184 America's Byways included in the KML.

## Data Notes

- The Byway list and designations come from FHWA America's Byways.
- Public ArcGIS geometry is used where available.
- Some routes use OSM/OSRM fallback geometry where ArcGIS geometry was unavailable.
- Fallback geometry is marked as `OSM补齐` in `americas_byways_coverage_report.csv`.

### Historic Route 66

`Historic Route 66` contains visible straight-line jumps in some areas.

Inspection confirmed those jumps already exist inside individual ArcGIS source `LineString` features. The KML preserves the source geometry and does not split or repair those source-data gaps.

## For Maintainers

Main generator:

```text
generate_americas_byways_kml.py
```

Endpoint place-name cache:

```text
endpoint_place_cache.json
```

The cache stores reverse-geocoded place names for start/end stars. Endpoint coordinates remain on the route; the place name is only used for the marker label.

Latest validation summary:

- FHWA America's Byways: 184
- Top-level state folders: 44
- Route Placemarks using `MultiGeometry`: 184
- Route `LineString` elements: 19700
- Endpoint star Point Placemarks: 582
- Missing geometry routes: 0
- All-American Roads: 38
- National Scenic Byways only: 146
