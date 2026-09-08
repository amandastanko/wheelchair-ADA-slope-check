# ADA Wheelchair Accessibility Along Line

An ArcGIS Pro Python toolbox tool that lets you draw a line anywhere on a
map and screen it for ADA running-slope accessibility, using real elevation
data.

## What it does

Drapes your drawn line over a DEM, computes running slope segment by
segment, and classifies each segment against ADA Standards for Accessible
Design (Section 405):

| Slope | Classification |
|---|---|
| ≤ 5% | Compliant - counts as an accessible route |
| 5-8.33% | Ramp range - permitted only as a ramp, needs landings every 30" of rise |
| > 8.33% | Non-compliant |

Output is a set of classified line segments (with average/min/max slope and
length per segment) plus a plain-language summary.

## What it does NOT do

This checks **running slope only**. It does not evaluate cross slope,
landing spacing/frequency, path width, or handrails - all of which factor
into real ADA compliance. **This is a first-pass screening tool, not a
compliance certification.**

## Elevation data - two modes

### Mode 1: Local DEM file (PROVEN, use this if in doubt)

Point the "Elevation surface (DEM)" parameter at a local raster file.
Confirmed working end-to-end.

**Use the folder/browse icon next to the parameter, not drag-and-drop.**
Dragging a raster layer from the Contents pane directly into this
parameter's text box is known to fail with `Error 000840: The value is
not a Raster Dataset` - even though the layer is a perfectly valid
raster. Click the folder icon and navigate to the actual file path
instead. If you hit that specific error, this is the fix, not a sign
something else is wrong.

**Pointing this parameter directly at a live ArcGIS Server/Online Image
Service URL does NOT work** - `arcpy.Raster()` has been confirmed to fail
opening a raw service URL this way, tested against two different hosted
elevation services.

#### Getting a local DEM for your area

- **US:** [USGS National Map Downloader](https://apps.nationalmap.gov/downloader/)
  has free 1-meter LiDAR-derived elevation (3DEP) for much of the country.
- **Local/regional:** many county or city GIS open data portals publish
  higher-resolution LiDAR DEMs (sometimes 1-3 ft) than USGS's national
  product.
- **Outside the US:** check your national or regional mapping agency's
  open data portal.

Clip whatever you download down to your area of interest first.

### Mode 2: Esri Global Elevation Service (online)

Instead of trying to open the raw Terrain ImageServer as a raster (the
approach that failed in Mode 1's problem case), this mode calls Esri's
dedicated **Elevation Analysis Profile service** directly via REST - the
same category of service the browser-based Elevation Profile widget uses.
It requests `DEMResolution=FINEST`, which automatically uses the best
resolution source available at your line's location.

**Tested and confirmed working** across three geographically diverse
locations (coastal Southern California, inland Southern California
foothills, and coastal Slovenia), each returning sensible, internally
consistent results - including resolution tiers that correctly matched
Esri's documented coverage (10m for US locations via USGS NED, 24m for
international locations via WorldDEM4Ortho, per Esri's published tier
list).

Requirements if you use this mode:
- **Sign in to ArcGIS Online specifically, not just an Enterprise
  portal.** This was tested and confirmed to matter: an Enterprise-only
  sign-in produced an "Invalid Token" error from the service; switching
  the active sign-in to an ArcGIS Online organizational account resolved
  it. If your Enterprise deployment is specifically configured to
  federate with ArcGIS Online's utility services, it may work there too -
  that combination hasn't been tested.
- Each run consumes your organization's service credits - check Esri's
  current credit reference before using this at any real scale

### A known real limitation of this mode

Esri's documentation states this service does not combine results across
areas with differing source data - if your line crosses into a region the
service treats as a separate data source, it can return multiple disjoint
result features rather than one continuous line. This tool handles that
case (processes each returned stretch separately, combines stats weighted
by length, and warns you in the output if it happened) - but it's still
worth knowing your route may have an actual coverage gap in the underlying
data, not just this tool's own limitation.




## Requirements

- ArcGIS Pro with the **3D Analyst extension** licensed (used for
  `InterpolateShape`)

## Setup

1. In ArcGIS Pro: `Insert > Toolbox > Add Toolbox`, browse to
   `ADA_Wheelchair_Accessibility.pyt`
2. (Optional) place `Class_Legend.lyrx` in the same folder as the `.pyt` to
   get automatic red/yellow/green symbology on the output
3. Open the tool, choose an **Elevation source** (Local DEM file or the
   online service), fill in the corresponding parameter, draw a line, run it

## Publishing as a web tool

This tool's input line parameter is a Feature Set, so it supports the
draw-on-map sketch experience in ArcGIS web clients (Map Viewer, Experience
Builder's Analysis widget). To make it available in a browser:

**This has only been tested for Mode 1 (local DEM).** Mode 2 (the online
elevation service) relies on `arcpy.GetSigninToken()`, which depends on an
active interactive Pro sign-in - whether that works the same way in a
published, server-side web tool execution context is untested. If you
want a published tool, use Mode 1.

1. Run the tool successfully once in Pro, with the DEM parameter locked to
   a specific local file (web tools can't use a raw live-service URL any
   more reliably than desktop Pro can, per the above)
2. Right-click the result in Geoprocessing History → **Share As Web Tool**
3. On the General tab, under Non-URL Data, choose **"Copy all data"** so
   your local DEM actually gets bundled into the published package -
   otherwise the server has no way to resolve your local file path
4. Add the published tool to an Experience Builder app's **Analysis
   widget** as a Custom web tool

Note: a web tool published this way is locked to whatever DEM was baked in
at publish time - it will only give meaningful results for that one
geographic area. For multiple areas, either publish separate tools per
area, or adapt the script to use a dropdown/choice parameter that maps a
friendly name to multiple pre-loaded local DEM paths.

## Limitations worth knowing

- 3D scene views in Experience Builder don't support freehand line
  sketching the way 2D map views do - you can only select existing line
  features as input, not draw live, when running this in 3D
- Slope math assumes horizontal and vertical units match after
  reprojection; the tool reprojects the input line to the DEM's coordinate
  system before measuring, but if your DEM's vertical units differ from
  its horizontal units, results will be wrong
- The online elevation service mode has only been tested running
  interactively in ArcGIS Pro with an active ArcGIS Online sign-in. It
  relies on `arcpy.GetSigninToken()`, which depends on that interactive
  session - whether it works the same way if this tool is published as a
  web tool and run server-side (no human signed in at execution time) is
  untested and genuinely unknown. If you need this to work as a published
  web tool, test that specific combination before relying on it; Mode 1
  (local DEM) is the one that's actually been confirmed to publish and
  run correctly server-side.
