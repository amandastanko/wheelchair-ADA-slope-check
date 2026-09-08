# ADA Wheelchair Accessibility Along Line - Notebook Version

An ArcGIS Notebook, published as a Web Tool, that lets you draw a line
anywhere on a map and screen it for ADA running-slope accessibility - no
local data required, runs entirely inside ArcGIS Online.

## How this differs from the Pro (.pyt) version

The [`pro-tool/`](../pro-tool) version supports a local DEM (more
accurate, works anywhere you can get elevation data for) and an online
elevation mode. This Notebook version supports **online elevation only** -
there's no meaningful way for a cloud notebook to "point at a local file"
on your machine.

**Important, tested finding:** the Pro tool's online mode was confirmed
to work when run interactively in Pro (signed into ArcGIS Online), but
confirmed to **fail** when published to an **ArcGIS Enterprise** server -
both a direct Enterprise Map Viewer test and an AGOL-registered-proxy test
of the same published tool failed identically with a
"User cannot be identified" error. This Notebook version was built
specifically to sidestep that, by running natively inside ArcGIS Online
with no Enterprise server in the execution path at all - and that fix
worked. If you only have Enterprise (no ArcGIS Online org), the Pro tool
run interactively in desktop Pro is your reliable option; this Notebook
version needs a real ArcGIS Online organizational account.

## What it does

Same classification logic as the Pro tool: drapes your drawn line over
elevation data, computes running slope segment by segment, classifies
each merged run against ADA Standards for Accessible Design (Section
405):

| Slope | Classification |
|---|---|
| ≤ 5% | Compliant |
| 5-8.33% | Ramp range - needs landings per ADA 405 |
| > 8.33% | Non-compliant |

Output includes average/min/max slope percent and length (meters and
feet) per segment, plus a plain-language summary. **Screening tool only -
does not evaluate cross slope, landing spacing, width, or handrails.**

## Requirements

- An ArcGIS Online organizational account (not Enterprise-only - see
  above)
- A notebook runtime capable of running `arcgis.gis.GIS("home")` and
  `gis.session` - this is standard across ArcGIS Online notebook
  runtimes, no special extension/licensing needed (unlike the Pro
  version's 3D Analyst requirement)
- Each run consumes your organization's service credits for the elevation
  analysis call - check Esri's current credit reference before using this
  at scale

## Setup

Full step-by-step setup, including the exact parameter configuration and
several real gotchas found while building this, are documented in the
header comment of `ADA_Wheelchair_Accessibility_Notebook.py` - read that
before starting, it will save you real time. Short version:

1. Create a notebook in your ArcGIS Online org
2. Paste the code from `ADA_Wheelchair_Accessibility_Notebook.py` across
   cells as described in its header comment
3. Define 3 input parameters and 2 output parameters via the notebook's
   Parameters panel
4. Use "Insert as variables" (input) and the output "+" buttons - **do
   not hand-edit these generated cells**, see the header comment for why
5. Publish, then test by running the published tool from Map Viewer with
   a real drawn line (not by running cells interactively - real parameter
   substitution only happens during an actual published job)

## Real gotchas found while building this (all documented in more detail
in the code's header comment)

- A real submitted line arrives in a nested
  `{"layerDefinition": {...}, "featureSet": {"features": [...]}}`
  structure, not a flat one - this tripped up both reading the input and,
  separately, getting the output to render as a visible map layer
- Hand-editing an "Insert as variables" generated cell can silently break
  parameter substitution, even when the edited code looks identical -
  delete and regenerate instead of editing
- `print()` output does not appear to surface in the published job's log
  - put debugging information directly into exception messages instead
- Output symbology (red/orange/green by ADA_Status) CAN be baked in via a
  `drawingInfo.renderer` block - confirmed working, no manual symbology
  setup needed when the result loads in Map Viewer

## Limitations worth knowing

- Online elevation resolution varies by location - can be as coarse as
  ~90m in poorly-covered areas, versus a few feet for a good local DEM.
  Esri's coverage index is queryable at
  `elevation.arcgis.com/arcgis/rest/services/WorldElevation/DataExtents/MapServer`
  if you want to check before trusting a result
- 3D scene views don't support freehand line sketching the way 2D map
  views do
- The elevation service does not combine results across areas with
  differing source data - a route crossing such a boundary can come back
  as multiple disjoint pieces rather than one continuous line (this tool
  handles that case and will tell you if it happened)
