# ADA Wheelchair Accessibility Along Line

Draw a line on a map, get back a first-pass ADA accessibility screening
of that route - real elevation data, real running-slope math, classified
against ADA Standards for Accessible Design (Section 405).

**This checks running slope only.** It does not evaluate cross slope,
landing spacing/frequency, path width, or handrails - all of which factor
into full ADA compliance. This is a screening tool, not a compliance
certification. Use it to flag routes worth a closer look, not as a final
answer.

## Two versions - pick based on what you have access to

| | [`pro-tool/`](pro-tool) | [`notebook-tool/`](notebook-tool) |
|---|---|---|
| Runs in | ArcGIS Pro (desktop) | ArcGIS Online (browser, cloud-native) |
| Elevation source | Local DEM (accurate) or online service | Online service only |
| Requires | 3D Analyst extension | ArcGIS Online org account |
| Publishing as a web tool | Local-DEM mode: works. Online mode: **confirmed to fail on ArcGIS Enterprise** | **Confirmed working** end-to-end, including auto-symbolized output |

**If you have ArcGIS Online:** the Notebook version is the more reliable
path to a browser-usable tool - it was built specifically because the Pro
tool's online mode does not work published to Enterprise, and this does.

**If you only have ArcGIS Enterprise, no ArcGIS Online org:** use the Pro
tool's local DEM mode, either run interactively in desktop Pro or
published as a web tool (this path is fully tested and reliable) - just
plan on sourcing a real DEM for your area of interest yourself (see that
tool's README for sources).

**If you want the best possible accuracy and don't need it in a browser:**
the Pro tool's local DEM mode, run interactively, using the highest-
resolution DEM you can get for your area (county/city LiDAR is often far
better than anything available online).

## Getting started

Read the README inside whichever folder matches your situation - each has
its own full setup instructions, requirements, and honestly-documented
gotchas found while building and testing these.
