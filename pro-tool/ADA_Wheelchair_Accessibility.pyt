"""
ADA Wheelchair Accessibility Along Line

Two elevation source modes:
  1. Local DEM file - proven working. Reprojects the input line to the
     DEM's coordinate system, drapes it with InterpolateShape, computes
     slope from X/Y/Z directly.
  2. Esri Global Elevation Service (online) - calls the same kind of
     Elevation Analysis "Profile" service the browser-based Elevation
     Profile widget uses (NOT arcpy.Raster() against the raw Terrain
     ImageServer, which is confirmed to fail). Tested and confirmed
     working across three geographically diverse locations (coastal
     Southern California, inland Southern California foothills, coastal
     Slovenia), with resolution results matching Esri's documented
     coverage tiers in each case. Requires signing in to ArcGIS Online
     specifically - an Enterprise-only sign-in was tested and confirmed
     to fail with an Invalid Token error.

REQUIREMENTS:
  - ArcGIS Pro with the 3D Analyst extension licensed (local DEM path only)
  - For the online path: signed in to an ArcGIS Online organizational
    account specifically (not just an Enterprise portal - see above).
    This consumes your org's service credits per run - check Esri's
    current credit reference before using it at scale.

ADA Standards for Accessible Design (2010), Section 405 (ramps) and 403
(walking surfaces):
  - Running slope <= 5%   -> counts as an accessible route (not a "ramp")
  - Running slope 5-8.33% -> allowed only as a ramp, requires landings
                             every 30 inches of rise
  - Running slope > 8.33% -> non-compliant, not permitted as an accessible
                             route or ramp without redesign
This tool does NOT check cross slope, landing spacing, handrails, or width -
those all also factor into real ADA compliance and are not evaluated here.
Treat this as a first-pass screening tool, not a compliance certification.
"""

import arcpy
import math
import os
import json
import urllib.request
import urllib.parse

ELEVATION_PROFILE_SERVICE = (
    "https://elevation.arcgis.com/arcgis/rest/services/Tools/ElevationSync/"
    "GPServer/Profile/execute"
)

# Esri's coverage index for the global elevation service - used as a
# fallback resolution check only if the Profile service's own response
# doesn't include a resolution field we can read directly.
RESOLUTION_LAYERS = [
    (1, "1m or better"), (2, "2-6m"), (3, "10m"), (4, "25m"),
    (5, "30m"), (6, "50-60m"), (7, "90m"), (8, "150m"),
    (9, "250m"), (10, "500m"), (11, "1000m"),
]
DATA_EXTENTS_SERVICE = (
    "https://elevation.arcgis.com/arcgis/rest/services/WorldElevation/"
    "DataExtents/MapServer"
)


def check_elevation_resolution(line_geom, wkid, messages):
    """Fallback coverage check against Esri's DataExtents index. Returns a
    label like '1m or better', or None if it can't be determined. Never
    raises - a failed check should not block the tool."""
    try:
        geom_json = line_geom.JSON
    except Exception:
        return None
    for layer_id, label in RESOLUTION_LAYERS:
        params = {
            "geometry": geom_json,
            "geometryType": "esriGeometryPolyline",
            "inSR": str(wkid),
            "spatialRel": "esriSpatialRelIntersects",
            "returnGeometry": "false",
            "returnCountOnly": "true",
            "f": "json",
        }
        url = f"{DATA_EXTENTS_SERVICE}/{layer_id}/query?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            if data.get("count", 0) > 0:
                return label
        except Exception as e:
            messages.addMessage(f"(Could not check elevation resolution coverage: {e})")
            return None
    return None


def classify(slope_pct, accessible_max, ramp_max):
    if slope_pct <= accessible_max:
        return "Compliant"
    elif slope_pct <= ramp_max:
        return "Ramp range (needs landings)"
    else:
        return "Non-compliant"


def add_segment_fields(out_fc):
    arcpy.management.AddField(out_fc, "ADA_Status", "TEXT", field_length=30)
    arcpy.management.AddField(out_fc, "AvgSlopePercent", "DOUBLE")
    arcpy.management.AddField(out_fc, "MinSlopePercent", "DOUBLE")
    arcpy.management.AddField(out_fc, "MaxSlopePercent", "DOUBLE")
    arcpy.management.AddField(out_fc, "Length_m", "DOUBLE")
    arcpy.management.AddField(out_fc, "Length_ft", "DOUBLE")


def insert_classified_segments(vertices, out_sr, out_fc, accessible_max,
                                ramp_max, meters_per_unit):
    """Processes ONE contiguous stretch of (x, y, z, cumulative_distance)
    vertices, inserting classified/merged segments into out_fc (fields must
    already exist - see add_segment_fields). `meters_per_unit` converts
    whatever linear unit the input distances are already in to meters -
    pass 1.0 if distances are already in meters (the online service path),
    or the DEM's spatial reference's own metersPerUnit for the local DEM
    path (which may be feet, meters, or something else depending on the
    DEM - never assume feet). Returns
    (max_slope, total_len_native, noncompliant_len_native, ramp_len_native)
    for this stretch, in the ORIGINAL native unit (not meters), so the
    caller can combine stats across multiple stretches correctly."""
    max_slope = 0.0
    total_len = 0.0
    noncompliant_len = 0.0
    ramp_len = 0.0

    insert_fields = ["SHAPE@", "ADA_Status", "AvgSlopePercent",
                      "MinSlopePercent", "MaxSlopePercent",
                      "Length_m", "Length_ft"]

    def finalize_run(ins_cursor, run_pts, run_slopes, run_horiz, status):
        if len(run_pts) < 2:
            return
        seg = arcpy.Polyline(arcpy.Array(run_pts), out_sr, True)
        run_len_native = sum(run_horiz)
        run_len_m = run_len_native * meters_per_unit
        run_len_ft = run_len_m / 0.3048
        avg_slope = (sum(s * h for s, h in zip(run_slopes, run_horiz)) / run_len_native
                     if run_len_native else 0)
        ins_cursor.insertRow([seg, status, avg_slope, min(run_slopes),
                              max(run_slopes), run_len_m, run_len_ft])

    with arcpy.da.InsertCursor(out_fc, insert_fields) as ins_cursor:
        run_pts, run_slopes, run_horiz, run_status = [], [], [], None

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

            p1 = arcpy.Point(x1, y1, z1)
            p2 = arcpy.Point(x2, y2, z2)

            if run_status is None:
                run_status, run_pts, run_slopes, run_horiz = status, [p1, p2], [slope_pct], [horiz]
            elif status == run_status:
                run_pts.append(p2)
                run_slopes.append(slope_pct)
                run_horiz.append(horiz)
            else:
                finalize_run(ins_cursor, run_pts, run_slopes, run_horiz, run_status)
                run_status, run_pts, run_slopes, run_horiz = status, [p1, p2], [slope_pct], [horiz]

        finalize_run(ins_cursor, run_pts, run_slopes, run_horiz, run_status)

    return max_slope, total_len, noncompliant_len, ramp_len


class Toolbox(object):
    def __init__(self):
        self.label = "ADA Accessibility Tools"
        self.alias = "adawheelchair"
        self.tools = [CheckWheelchairAccessibility]


class CheckWheelchairAccessibility(object):
    def __init__(self):
        self.label = "Check Wheelchair Accessibility Along Line"
        self.description = (
            "Drapes a user-drawn line over elevation data (a local DEM, or "
            "Esri's online Elevation Analysis service) and classifies "
            "segments against ADA running-slope thresholds. Screening "
            "tool only - does not check cross slope, landing spacing, "
            "width, or handrails."
        )
        self.canRunInBackground = False

    def getParameterInfo(self):
        in_line = arcpy.Parameter(
            displayName="Path to check (draw a line)",
            name="in_line",
            datatype="GPFeatureRecordSetLayer",
            parameterType="Required",
            direction="Input")
        template = arcpy.management.CreateFeatureclass(
            "in_memory", "line_template", "POLYLINE", spatial_reference=4326)[0]
        in_line.value = template

        elevation_source = arcpy.Parameter(
            displayName="Elevation source",
            name="elevation_source",
            datatype="GPString",
            parameterType="Required",
            direction="Input")
        elevation_source.filter.type = "ValueList"
        elevation_source.filter.list = [
            "Local DEM file",
            "Esri Global Elevation Service (online)",
        ]
        elevation_source.value = "Esri Global Elevation Service (online)"

        in_dem = arcpy.Parameter(
            displayName="Elevation surface (DEM) - required if using a local DEM",
            name="in_dem",
            datatype="DERasterDataset",
            parameterType="Optional",
            direction="Input")

        sample_distance = arcpy.Parameter(
            displayName="Segment length for sampling (DEM's linear unit; "
                         "ignored for the online source, which auto-samples)",
            name="sample_distance",
            datatype="GPDouble",
            parameterType="Optional",
            direction="Input")
        sample_distance.value = 5.0

        accessible_max = arcpy.Parameter(
            displayName="Max accessible running slope % (ADA default 5.0)",
            name="accessible_max",
            datatype="GPDouble",
            parameterType="Optional",
            direction="Input")
        accessible_max.value = 5.0

        ramp_max = arcpy.Parameter(
            displayName="Max ramp slope % (ADA default 8.33)",
            name="ramp_max",
            datatype="GPDouble",
            parameterType="Optional",
            direction="Input")
        ramp_max.value = 8.33

        out_segments = arcpy.Parameter(
            displayName="Classified segments",
            name="out_segments",
            datatype="DEFeatureClass",
            parameterType="Derived",
            direction="Output")
        lyrx_path = os.path.join(os.path.dirname(__file__), "Class_Legend.lyrx")
        if os.path.exists(lyrx_path):
            out_segments.symbology = lyrx_path

        out_summary = arcpy.Parameter(
            displayName="Summary",
            name="out_summary",
            datatype="GPString",
            parameterType="Derived",
            direction="Output")

        return [in_line, elevation_source, in_dem, sample_distance,
                accessible_max, ramp_max, out_segments, out_summary]

    def _get_vertices_from_local_dem(self, in_line, in_dem, step, messages):
        """PROVEN PATH - unchanged from the version already tested and
        confirmed working."""
        try:
            dem_sr = arcpy.Raster(in_dem).spatialReference
        except RuntimeError as e:
            raise arcpy.ExecuteError(
                f"Could not open '{in_dem}' as a raster. This must be a "
                f"local file or a raster layer already added to your map, "
                f"not a live Image Service URL typed directly - those are "
                f"not supported here. Original error: {e}"
            )
        if dem_sr is None or dem_sr.name == "Unknown":
            raise arcpy.ExecuteError(
                f"Could not determine a valid spatial reference for the "
                f"input DEM ({in_dem}). Check the raster's properties in Pro."
            )
        messages.addMessage(f"Working in DEM spatial reference: {dem_sr.name}")

        has_3d = arcpy.CheckExtension("3D") == "Available"
        if not has_3d:
            raise arcpy.ExecuteError(
                "The 3D Analyst extension is required for InterpolateShape "
                "and is not available/licensed in this environment."
            )
        arcpy.CheckOutExtension("3D")
        try:
            projected_line = arcpy.management.Project(
                in_line, "in_memory/projected_line", dem_sr)[0]
            interpolated = arcpy.ddd.InterpolateShape(
                in_dem, projected_line, "in_memory/interpolated_line",
                sample_distance=step
            )[0]

            vertices = []
            cum_dist = 0.0
            with arcpy.da.SearchCursor(interpolated, ["SHAPE@"]) as cur:
                for row in cur:
                    for part in row[0]:
                        pts = [pt for pt in part if pt]
                        for i, pt in enumerate(pts):
                            if i > 0:
                                prev = pts[i - 1]
                                cum_dist += math.hypot(pt.X - prev.X, pt.Y - prev.Y)
                            z = pt.Z if pt.Z is not None else 0
                            vertices.append((pt.X, pt.Y, z, cum_dist))
            meters_per_unit = dem_sr.metersPerUnit or 1.0
            messages.addMessage(
                f"DEM linear unit: {dem_sr.linearUnitName} "
                f"({meters_per_unit} meters per unit)"
            )
            return [vertices], dem_sr, [], meters_per_unit
        finally:
            arcpy.CheckInExtension("3D")

    def _get_vertices_from_online_service(self, in_line, messages):
        """Calls Esri's Elevation Analysis Profile service directly via
        REST, the same category of service the browser Elevation Profile
        widget uses, rather than arcpy.Raster() against the raw Terrain
        ImageServer (confirmed to fail). Tested and confirmed working
        across three geographically diverse locations. Requires signing
        in to ArcGIS Online specifically (an Enterprise-only sign-in was
        tested and confirmed NOT to work, returning an Invalid Token
        error) - if this fails or returns something unexpected, the raw
        response is printed to messages so it can be debugged."""
        try:
            token_info = arcpy.GetSigninToken()
        except Exception as e:
            raise arcpy.ExecuteError(
                f"Could not get a sign-in token (arcpy.GetSigninToken() "
                f"failed: {e}). The online elevation source requires being "
                f"signed in to an ArcGIS Online or Enterprise account in "
                f"this Pro session."
            )
        if not token_info or "token" not in token_info:
            raise arcpy.ExecuteError(
                "No active sign-in token available. Sign in to your "
                "ArcGIS Online or Enterprise account in Pro, or use the "
                "Local DEM file option instead."
            )
        token = token_info["token"]
        referer = token_info.get("referer", "")

        with arcpy.da.SearchCursor(in_line, ["SHAPE@"]) as cur:
            line_geom = next(cur)[0]
        in_sr = line_geom.spatialReference
        wkid = in_sr.factoryCode if in_sr else 4326

        paths = []
        for part in line_geom:
            paths.append([[pt.X, pt.Y] for pt in part if pt])

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
            "token": token,
        }
        url = ELEVATION_PROFILE_SERVICE + "?" + urllib.parse.urlencode(params)

        try:
            req = urllib.request.Request(url, headers={"Referer": referer})
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode()
        except Exception as e:
            raise arcpy.ExecuteError(
                f"Request to the online elevation service failed: {e}"
            )

        try:
            data = json.loads(raw)
        except Exception:
            raise arcpy.ExecuteError(
                f"Could not parse the elevation service's response as "
                f"JSON. Raw response: {raw[:2000]}"
            )

        if "error" in data:
            raise arcpy.ExecuteError(
                f"Elevation service returned an error: {data['error']}"
            )

        # Best guess at the response structure for a synchronous GP execute
        # feature-set output - verify this against the real response.
        try:
            results = data["results"]
            output_param = next(
                r for r in results
                if r.get("dataType", "").startswith("GPFeatureRecordSetLayer")
            )
            out_value = output_param["value"]
            out_sr_info = out_value.get("spatialReference", {"wkid": wkid})
            out_wkid = out_sr_info.get("wkid", wkid)
            out_features = out_value["features"]
            if not out_features:
                raise arcpy.ExecuteError(
                    "The elevation service returned zero features for "
                    "this line - it may fall outside the service's "
                    "coverage area entirely."
                )
        except arcpy.ExecuteError:
            raise
        except Exception as e:
            messages.addMessage(f"Full raw response for debugging: {raw[:4000]}")
            raise arcpy.ExecuteError(
                f"Response from the elevation service didn't match the "
                f"expected structure ({e}). The raw response has been "
                f"printed above - this needs to be checked against what "
                f"the service actually returned."
            )

        if len(out_features) > 1:
            messages.addWarningMessage(
                f"The elevation service returned {len(out_features)} "
                f"separate features for this one line - it does not "
                f"combine results across areas with differing source "
                f"data. Each is being processed as its own stretch; "
                f"there may be a gap in coverage between them."
            )

        out_sr = arcpy.SpatialReference(out_wkid)
        stretches = []
        resolution_labels = []
        for out_feature in out_features:
            out_paths = out_feature["geometry"]["paths"]
            label = out_feature.get("attributes", {}).get(
                "DEM_Resolution") or out_feature.get("attributes", {}).get(
                "DEMResolution")
            if label:
                resolution_labels.append(str(label))

            vertices = []
            for path in out_paths:
                for vertex in path:
                    # each vertex expected as [x, y, z, m]
                    x, y = vertex[0], vertex[1]
                    z = vertex[2] if len(vertex) > 2 else 0
                    m = vertex[3] if len(vertex) > 3 else 0
                    vertices.append((x, y, z, m))
            if len(vertices) >= 2:
                stretches.append(vertices)

        # M-values from this service are documented to always be in meters,
        # regardless of input/output spatial reference.
        return stretches, out_sr, resolution_labels, 1.0

    def execute(self, parameters, messages):
        in_line = parameters[0].value
        elevation_source = parameters[1].valueAsText
        in_dem = parameters[2].valueAsText
        step = parameters[3].value or 5.0
        accessible_max = parameters[4].value or 5.0
        ramp_max = parameters[5].value or 8.33

        arcpy.env.overwriteOutput = True
        use_online = elevation_source.startswith("Esri Global")

        if use_online:
            messages.addWarningMessage(
                "Using the online elevation service (ArcGIS Online sign-in "
                "required). Resolution varies by location - check the "
                "summary output."
            )
            stretches, out_sr, resolution_labels, meters_per_unit = self._get_vertices_from_online_service(
                in_line, messages)
            if not resolution_labels:
                # fall back to the DataExtents coverage check if the
                # service's own response didn't include a resolution field
                fallback = check_elevation_resolution(
                    in_line if hasattr(in_line, "JSON") else None,
                    out_sr.factoryCode, messages)
                if fallback:
                    resolution_labels = [fallback]
        else:
            if not in_dem:
                raise arcpy.ExecuteError(
                    "Elevation surface (DEM) is required when Elevation "
                    "Source is set to 'Local DEM file'."
                )
            stretches, out_sr, resolution_labels, meters_per_unit = self._get_vertices_from_local_dem(
                in_line, in_dem, step, messages)

        out_fc = arcpy.management.CreateFeatureclass(
            arcpy.env.scratchGDB, "classified_segments", "POLYLINE",
            spatial_reference=out_sr, has_z="ENABLED"
        )[0]
        add_segment_fields(out_fc)

        # Process each stretch separately (there may be more than one if
        # the online service returned disjoint pieces), then combine stats
        # weighted by each stretch's own length - a short stretch shouldn't
        # count as heavily toward the overall percentages as a long one.
        max_slope = 0.0
        total_len = 0.0
        noncompliant_len = 0.0
        ramp_len = 0.0
        for vertices in stretches:
            stretch_max, stretch_total, stretch_noncompliant, stretch_ramp = (
                insert_classified_segments(vertices, out_sr, out_fc,
                                            accessible_max, ramp_max,
                                            meters_per_unit)
            )
            max_slope = max(max_slope, stretch_max)
            total_len += stretch_total
            noncompliant_len += stretch_noncompliant
            ramp_len += stretch_ramp

        pct_noncompliant = (noncompliant_len / total_len * 100) if total_len else 0
        pct_ramp = (ramp_len / total_len * 100) if total_len else 0
        pct_compliant = 100 - pct_noncompliant - pct_ramp

        summary = (
            f"Max slope along route: {max_slope:.1f}%. "
            f"{pct_compliant:.0f}% of the route is a fully compliant "
            f"accessible route (<= {accessible_max}%). "
            f"{pct_ramp:.0f}% falls in the ramp range "
            f"({accessible_max}-{ramp_max}%) and would need landings "
            f"per ADA 405. "
            f"{pct_noncompliant:.0f}% exceeds {ramp_max}% and is "
            f"non-compliant even as a ramp. "
            f"Note: this checks running slope only - not cross slope, "
            f"landing spacing, width, or handrails."
        )
        if len(stretches) > 1:
            summary += (
                f" NOTE: the elevation source returned {len(stretches)} "
                f"disjoint stretches for this line, not one continuous "
                f"route - there may be a coverage gap partway along it."
            )
        if resolution_labels:
            summary += f" Elevation source resolution: {', '.join(sorted(set(resolution_labels)))}."
        messages.addMessage(summary)

        parameters[6].value = out_fc
        parameters[7].value = summary
