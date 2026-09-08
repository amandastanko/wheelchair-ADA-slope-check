"""
ADA Wheelchair Accessibility Along Line - ArcGIS Notebook version

Draws a user-sketched line and returns ADA running-slope compliance for
each segment, using Esri's global online elevation service - no local DEM
required. Runs entirely inside ArcGIS Online as a published Notebook Web
Tool. Tested and confirmed working end-to-end: real elevation data, real
classification, real color-coded output symbology (green/orange/red),
rendering directly in Map Viewer with no manual setup.

Companion to a separate ArcGIS Pro (.pyt) version that supports both a
local DEM and this same online service - see the main README for when to
use which. The key practical difference: this Notebook version has been
confirmed to work published as a fully cloud-native web tool; the Pro
version's online mode was confirmed NOT to work when published to an
Enterprise server (failed with identity/auth errors both times tested) -
Enterprise-server execution and AGOL-native Notebook execution are
genuinely different environments for this purpose, not interchangeable.

HOW THIS WAS SET UP (matches what was actually tested working):

1. Create a new notebook in your ArcGIS Online org.
2. Cell 1: the notebook's own auto-generated `GIS("home")` welcome cell.
3. Cell 2: paste everything below down through the end of
   `build_classified_output()` - all imports and function definitions.
4. Define parameters via the notebook's Parameters panel:
     Input:  "Path to check"              -> Feature set (polyline)
                                              Default value must be valid
                                              JSON, e.g.:
                                              {"geometryType": "esriGeometryPolyline",
                                               "spatialReference": {"wkid": 4326},
                                               "features": []}
     Input:  "Max accessible running slope %" -> Double, default 5.0
     Input:  "Max ramp slope %"               -> Double, default 8.33
     Output: "Classified segments"        -> Feature set (needs default
                                              JSON too - same shape plus a
                                              "fields" array; see below)
     Output: "Summary"                    -> String (default: any
                                              placeholder text)
5. Cell 3: click into an EMPTY cell, then use the Parameters panel's
   Input tab -> "Insert as variables". Do NOT hand-type or edit this
   cell's contents afterward - the platform appears to link parameter
   substitution to this specific auto-generated cell, and editing it
   (even just retyping identical-looking code) can silently break that
   link, causing the tool to always receive default/placeholder values
   even when a user submits real input. If you need to change anything,
   delete the parameter and cell entirely and redo "Insert as variables"
   fresh rather than editing by hand.
6. Cell 4: the main computation - see the bottom of this file. Uses
   whatever variable names Insert as variables actually generated (this
   was "Path_to_Check", not "input_line" - it will match your parameter's
   Variable Name field, not its display name).
7. Cell 5: click into an empty cell, use the Parameters panel's Output
   tab, click the "+" next to each output parameter to insert its
   auto-generated output-writing snippet.
8. Publish via the Publish button. Test by running the published tool
   from Map Viewer's Analysis panel with a real drawn line - running
   cells interactively in the editor will always show placeholder/default
   values, since real substitution only happens during an actual
   published job execution.

REAL STRUCTURAL FINDINGS FROM TESTING (not assumptions - confirmed from
actual job run error output):

- A real submitted line arrives wrapped as
  {"layerDefinition": {...}, "featureSet": {"features": [...]}} - NOT a
  flat {"geometryType":..., "features": [...]} structure. Map Viewer's
  Analysis widget sketch tool uses this convention for both input AND
  (per testing) expected output - a flat structure was accepted without
  error but never rendered as a visible layer in Map Viewer's results.
- Coordinates come back in whatever CRS the map was in when the line was
  drawn (Web Mercator/102100 in testing), not necessarily WGS84 - the
  code below reads the actual wkid rather than assuming one.
- Symbology CAN be baked into the output via a standard
  "drawingInfo.renderer" block inside layerDefinition (the older,
  broadly-supported Esri renderer JSON format, not the newer CIM format
  used by .lyrx files) - confirmed working, color-coded output rendered
  correctly with zero manual symbology setup needed in Map Viewer.
- print() statements do not appear to surface anywhere in the published
  job's log output (Results/Messages/Parameters tabs) - putting
  diagnostic info directly into exception messages was the only
  reliable way found to debug a failed run.
"""

import json
import math
from arcgis.gis import GIS
from arcgis.features import FeatureSet

ELEVATION_PROFILE_SERVICE = (
    "https://elevation.arcgis.com/arcgis/rest/services/Tools/ElevationSync/"
    "GPServer/Profile/execute"
)


def classify(slope_pct, accessible_max, ramp_max):
    if slope_pct <= accessible_max:
        return "Compliant"
    elif slope_pct <= ramp_max:
        return "Ramp range (needs landings)"
    else:
        return "Non-compliant"


def get_line_geometry_and_sr(input_line):
    """Extract paths (list of [x,y] point lists) and a spatial reference
    wkid from an input Feature Set parameter. A real submitted sketch from
    Map Viewer's Analysis widget arrives wrapped as
    {"layerDefinition": {...}, "featureSet": {"features": [...]}} - NOT a
    flat {"features": [...]} structure. This was confirmed from a real
    published job's error output after earlier flat-structure parsing
    always found zero features (the actual root cause of every "no
    features" error hit during testing, not a drawing or parameter-passing
    problem). Handles both the real wrapped shape and a flat shape as a
    fallback, in case a different client submits it differently."""
    raw_repr = repr(input_line)[:1000]
    raw_type = type(input_line).__name__

    if isinstance(input_line, FeatureSet):
        fs_dict = input_line.to_dict()
    elif isinstance(input_line, str):
        try:
            fs_dict = json.loads(input_line)
        except Exception:
            raise ValueError(
                f"Path_to_Check arrived as a string but could not be "
                f"parsed as JSON. Type={raw_type}, raw value={raw_repr}"
            )
    elif isinstance(input_line, dict):
        fs_dict = input_line
    else:
        try:
            fs_dict = input_line.to_dict()
        except Exception:
            raise ValueError(
                f"Don't know how to handle Path_to_Check of type "
                f"{raw_type}. Raw value: {raw_repr}"
            )

    if "featureSet" in fs_dict:
        actual_fs = fs_dict["featureSet"]
        sr = (fs_dict.get("layerDefinition", {}).get("spatialReference")
              or actual_fs.get("spatialReference")
              or {"wkid": 4326})
    else:
        actual_fs = fs_dict
        sr = fs_dict.get("spatialReference", {"wkid": 4326})

    wkid = sr.get("wkid") or sr.get("latestWkid") or 4326

    features = actual_fs.get("features", [])
    if not features:
        raise ValueError(
            f"Input line has no features - was a line actually drawn? "
            f"Received type={raw_type}, raw value={raw_repr}"
        )

    try:
        paths = features[0]["geometry"]["paths"]
    except KeyError as e:
        raise ValueError(
            f"Feature geometry didn't contain the expected 'paths' key "
            f"({e}). Raw geometry: {repr(features[0].get('geometry'))[:500]}"
        )

    return paths, wkid


def call_elevation_profile_service(gis, paths, wkid):
    """Calls Esri's Elevation Analysis Profile service using the notebook's
    own authenticated session. Returns the raw parsed JSON response."""
    feature_set = {
        "geometryType": "esriGeometryPolyline",
        "spatialReference": {"wkid": wkid},
        "features": [{"geometry": {"paths": paths}}],
    }

    params = {
        "InputLineFeatures": json.dumps(feature_set),
        "DEMResolution": "FINEST",
        "returnZ": "true",
        "returnM": "true",
        "f": "json",
    }

    # gis.session is the documented public way to make authenticated
    # requests as the notebook's active identity - untested against this
    # specific external premium service, verify this actually attaches
    # valid credentials rather than hitting the same identity wall we saw
    # from the Enterprise-published version.
    resp = gis.session.get(ELEVATION_PROFILE_SERVICE, params=params, timeout=60)
    raw = resp.text

    try:
        data = json.loads(raw)
    except Exception:
        raise RuntimeError(f"Could not parse elevation service response as JSON. "
                            f"Raw response: {raw[:2000]}")

    if "error" in data:
        raise RuntimeError(f"Elevation service returned an error: {data['error']}. "
                            f"Full raw response: {raw[:2000]}")

    return data


def extract_stretches_from_response(data, fallback_wkid):
    """Parses the Profile service's response into a list of stretches,
    each a list of (x, y, z, m) vertex tuples. Handles multiple returned
    features (the service does not combine differing-resolution regions).
    Best guess at response structure - print raw response if this fails."""
    results = data["results"]
    output_param = next(
        r for r in results
        if r.get("dataType", "").startswith("GPFeatureRecordSetLayer")
    )
    out_value = output_param["value"]
    out_sr_info = out_value.get("spatialReference", {"wkid": fallback_wkid})
    out_wkid = out_sr_info.get("wkid", fallback_wkid)
    out_features = out_value.get("features", [])

    if not out_features:
        raise RuntimeError("Elevation service returned zero features for this line - "
                            "it may be outside the service's coverage area.")

    stretches = []
    resolution_labels = []
    for feat in out_features:
        label = feat.get("attributes", {}).get("DEM_Resolution") or \
            feat.get("attributes", {}).get("DEMResolution")
        if label:
            resolution_labels.append(str(label))

        vertices = []
        for path in feat["geometry"]["paths"]:
            for v in path:
                x, y = v[0], v[1]
                z = v[2] if len(v) > 2 else 0
                m = v[3] if len(v) > 3 else 0
                vertices.append((x, y, z, m))
        if len(vertices) >= 2:
            stretches.append(vertices)

    return stretches, out_wkid, resolution_labels


def build_classified_output(stretches, out_wkid, accessible_max, ramp_max):
    """Walks each stretch's vertices (already in meters for both Z and M,
    per Esri's documentation of this service), classifies segments,
    merges consecutive same-status runs, and builds an output feature
    list (plain Esri JSON, no arcpy). Returns
    (output_features_list, max_slope, pct_compliant, pct_ramp, pct_noncompliant)."""
    output_features = []
    max_slope = 0.0
    total_len = 0.0
    noncompliant_len = 0.0
    ramp_len = 0.0

    for vertices in stretches:
        run_pts, run_slopes, run_horiz, run_status = [], [], [], None

        def finalize_run(pts, slopes, horiz, status):
            if len(pts) < 2:
                return
            run_len = sum(horiz)
            avg_slope = sum(s * h for s, h in zip(slopes, horiz)) / run_len if run_len else 0
            output_features.append({
                "geometry": {"paths": [[[p[0], p[1]] for p in pts]]},
                "attributes": {
                    "ADA_Status": status,
                    "AvgSlopePercent": avg_slope,
                    "MinSlopePercent": min(slopes),
                    "MaxSlopePercent": max(slopes),
                    "Length_m": run_len,
                    "Length_ft": run_len / 0.3048,
                }
            })

        for i in range(len(vertices) - 1):
            x1, y1, z1, d1 = vertices[i]
            x2, y2, z2, d2 = vertices[i + 1]
            horiz = d2 - d1
            if horiz <= 0:
                continue
            slope_pct = abs((z2 - z1) / horiz) * 100.0
            status = classify(slope_pct, accessible_max, ramp_max)

            total_len += horiz
            if status == "Non-compliant":
                noncompliant_len += horiz
            elif status.startswith("Ramp"):
                ramp_len += horiz
            max_slope = max(max_slope, slope_pct)

            p1, p2 = (x1, y1), (x2, y2)
            if run_status is None:
                run_status, run_pts, run_slopes, run_horiz = status, [p1, p2], [slope_pct], [horiz]
            elif status == run_status:
                run_pts.append(p2)
                run_slopes.append(slope_pct)
                run_horiz.append(horiz)
            else:
                finalize_run(run_pts, run_slopes, run_horiz, run_status)
                run_status, run_pts, run_slopes, run_horiz = status, [p1, p2], [slope_pct], [horiz]

        finalize_run(run_pts, run_slopes, run_horiz, run_status)

    pct_noncompliant = (noncompliant_len / total_len * 100) if total_len else 0
    pct_ramp = (ramp_len / total_len * 100) if total_len else 0
    pct_compliant = 100 - pct_noncompliant - pct_ramp

    return output_features, max_slope, pct_compliant, pct_ramp, pct_noncompliant


# ============================================================
# MAIN COMPUTATION CELL - this is what actually ran as "Cell 4" above.
# Path_to_Check, accessible_max, ramp_max are generated by the
# "Insert as variables" cell (Cell 3) that must run before this one.
# ============================================================

gis = GIS("home")

paths, wkid = get_line_geometry_and_sr(Path_to_Check)
response = call_elevation_profile_service(gis, paths, wkid)
stretches, out_wkid, resolution_labels = extract_stretches_from_response(response, wkid)
output_features, max_slope, pct_compliant, pct_ramp, pct_noncompliant = (
    build_classified_output(stretches, out_wkid, accessible_max, ramp_max)
)

summary = (
    f"Max slope along route: {max_slope:.1f}%. "
    f"{pct_compliant:.0f}% of the route is a fully compliant accessible route "
    f"(<= {accessible_max}%). {pct_ramp:.0f}% falls in the ramp range "
    f"({accessible_max}-{ramp_max}%) and would need landings per ADA 405. "
    f"{pct_noncompliant:.0f}% exceeds {ramp_max}% and is non-compliant even as "
    f"a ramp. Note: this checks running slope only - not cross slope, "
    f"landing spacing, width, or handrails."
)
if len(stretches) > 1:
    summary += (
        f" NOTE: the elevation source returned {len(stretches)} disjoint "
        f"stretches for this line - there may be a coverage gap partway along it."
    )
if resolution_labels:
    summary += f" Elevation source resolution: {', '.join(sorted(set(resolution_labels)))}."

# The summary text works correctly as a flat JSON string, but the
# classified segments (Feature set output) were not rendering as a map
# result even though the run completed successfully - likely because Map
# Viewer expects OUTPUT feature sets wrapped the same way it wraps INPUT
# sketches (confirmed from a real Path_to_Check payload):
# {"layerDefinition": {...}, "featureSet": {"features": [...]}}
# rather than a flat {"geometryType":..., "features":[...]} structure.
# This assigns a real OBJECTID to each feature too, since the
# layerDefinition declares objectIdField.
numbered_features = [
    {**f, "attributes": {**f["attributes"], "OBJECTID": i + 1}}
    for i, f in enumerate(output_features)
]

classified_segments_output = json.dumps({
    "layerDefinition": {
        "geometryType": "esriGeometryPolyline",
        "objectIdField": "OBJECTID",
        "spatialReference": {"wkid": out_wkid},
        "fields": [
            {"name": "OBJECTID", "type": "esriFieldTypeOID", "alias": "OBJECTID"},
            {"name": "ADA_Status", "type": "esriFieldTypeString", "alias": "ADA_Status", "length": 30},
            {"name": "AvgSlopePercent", "type": "esriFieldTypeDouble", "alias": "AvgSlopePercent"},
            {"name": "MinSlopePercent", "type": "esriFieldTypeDouble", "alias": "MinSlopePercent"},
            {"name": "MaxSlopePercent", "type": "esriFieldTypeDouble", "alias": "MaxSlopePercent"},
            {"name": "Length_m", "type": "esriFieldTypeDouble", "alias": "Length_m"},
            {"name": "Length_ft", "type": "esriFieldTypeDouble", "alias": "Length_ft"},
        ],
        # Standard Esri "drawingInfo.renderer" convention - the older,
        # widely-supported symbology format (not the newer CIM format the
        # .lyrx file used), documented as the way Feature Collections carry
        # their own symbology without needing a saved hosted layer.
        "drawingInfo": {
            "renderer": {
                "type": "uniqueValue",
                "field1": "ADA_Status",
                "uniqueValueInfos": [
                    {
                        "value": "Compliant",
                        "label": "Compliant",
                        "symbol": {
                            "type": "esriSLS",
                            "style": "esriSLSSolid",
                            "color": [0, 200, 0, 255],
                            "width": 3,
                        },
                    },
                    {
                        "value": "Ramp range (needs landings)",
                        "label": "Ramp range (needs landings)",
                        "symbol": {
                            "type": "esriSLS",
                            "style": "esriSLSSolid",
                            "color": [255, 165, 0, 255],
                            "width": 3,
                        },
                    },
                    {
                        "value": "Non-compliant",
                        "label": "Non-compliant",
                        "symbol": {
                            "type": "esriSLS",
                            "style": "esriSLSSolid",
                            "color": [255, 0, 0, 255],
                            "width": 3,
                        },
                    },
                ],
                "defaultSymbol": {
                    "type": "esriSLS",
                    "style": "esriSLSSolid",
                    "color": [128, 128, 128, 255],
                    "width": 2,
                },
            }
        },
    },
    "featureSet": {
        "geometryType": "esriGeometryPolyline",
        "spatialReference": {"wkid": out_wkid},
        "features": numbered_features,
    },
})
summary_output = summary
