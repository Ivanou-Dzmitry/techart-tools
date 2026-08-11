# TechArt Tools

**TechArt Tools** is a Blender 5.2+ extension that brings a set of technical-art checks and UV utilities into one place. It is built for game-art and hard-surface workflows: cleaning up UVs, verifying texel density, spotting distortion and flipped normals, and running a quick QA pass on a model before it goes for feedback or export.

The tool lives in two places, each opening with an **Online Guide** button linking back to this page:

* **UV Editor sidebar (N-panel → TechArt Tools tab)** — three collapsible sections: **UV Manipulation** (transforms), **Checkers** (checker textures, Render UV, materials) and **Texel** (UV utilization, texel density). Collapsed by default so the sidebar stays short — expand only what you need.
* **3D Viewport sidebar (N-panel → TechArt Tools tab)** — the **Checker**, a 13-point QA checklist for the selected object(s).

A **Tips** section appears at the bottom of the UV Editor tab after most actions, with a short, contextual explanation of what just happened and why it matters.

Source code: [github.com/Ivanou-Dzmitry/techart-tools](https://github.com/Ivanou-Dzmitry/techart-tools)

## Requirements and Limitations

* Works with Mesh objects only.
* Most UV operations require Edit Mode with an active UV map.
* The Checker (QA checklist) requires Object Mode.
* Some functions work on a single object, others accept a multi-object selection — this is noted per feature below.

---

## UV Manipulation

### Rotate

Rotates the selected UVs around their median point by a fixed angle: **-90 / -45 / +45 / +90** degrees. Positive values rotate counter-clockwise. Useful for straightening seams or fixing a wrong texture orientation before packing.

### Scale

Scales the selected UVs around their median point: **x0.25 / x0.5 / x2 / x4**. A quick way to balance texel density between UV islands without leaving Edit Mode.

### Move UV

Shifts the selected UVs by exactly one full tile along U or V: **-1U / +1U / -1V / +1V**. Handy for tiling textures or moving an island onto a neighboring UV tile.

### Align

* **V-align** — snaps all selected UVs to a single X coordinate (the median), producing a vertical line.
* **H-align** — snaps all selected UVs to a single Y coordinate (the median), producing a horizontal line.

Useful for straightening tileable trims and repeating patterns.

---

## Checkers

A checker is a temporary texture applied to a model to visually inspect the UV layout before the final texture exists. TechArt Tools ships four:

* **Standard** — reveals stretching and pinching on the UV layout. The most commonly used checker.
* **Digital** — numbers make it easy to spot mirrored or flipped UV islands, which matters whenever the final texture will carry text.
* **Diagonal** — good for checking that seams line up and UV direction is consistent across neighboring islands. Mainly useful for camouflage-style textures.
* **Gradient** — shows roughly how UV space is distributed across the whole 0–1 tile. Does not tile.

Clicking a checker thumbnail assigns it to the selected object(s) and automatically switches the viewport to Material Preview shading so the result is immediately visible.

### Texture Size

Retiles the active checker to simulate a texture resolution: **128 / 256 / 512 / 1K / 2K / 4K / 8K**. This changes how many times the checker pattern repeats across the UV tile, matching what the real texture would look like at that resolution. Affects every tileable checker material in the scene (Gradient is excluded, since it is not meant to tile).

### Render UV

Renders the current UV layout (edges over a translucent fill) to an image and applies it to the object as a texture, so you can see the UV layout directly on the model without opening the UV Editor — useful for spotting missing or broken UVs at a glance.

### Material

* **Gloss** — assigns a glossy material (low roughness). Sharp specular highlights make it easier to spot faceting and other surface artifacts.
* **Matte** — assigns a matte material (high roughness). Neutral and easy on the eyes — good for a general shape and silhouette review.
* **NM** — assigns a test normal map with readable "UP" / "DOWN" text baked into it. This reveals a flipped Y channel or mirrored UVs at a glance — something a generic bump pattern cannot show. Replace it with your own normal map once you have one.
* **Reset** — removes all materials from the selected object(s).

---

## Texel

Groups everything about measuring and controlling how texture space is used: overall UV utilization and per-face texel density.

### UV Utilization

Reports what percentage of the 0–1 UV tile is actually covered by the mesh's UVs. The check renders the UV layout to an opaque-filled image and counts non-transparent pixels, so overlapping islands are **not** double-counted — a plain polygon-area sum would over-report coverage whenever UVs intentionally overlap (e.g. mirrored parts sharing texture space).

A thumbnail preview of the coverage mask is shown alongside the result. Rough guidance:

| Coverage | Meaning |
| :---- | :---- |
| < 25% | Poor — most of the texture space is wasted. |
| 25–33% | Low — packing could be noticeably tighter. |
| 33–60% | Moderate — there is still room to pack tighter. |
| 60–75% | Good. |
| 75–90% | Excellent. |
| > 90% | Very high — make sure there is still enough padding between islands to avoid texture bleeding. |

Near-zero coverage almost always means the UV islands are positioned outside the 0–1 tile — this check only counts what falls inside it.

### Texel Density

Texel density is the number of texture pixels that map onto one meter of real-world surface (px/m). It depends on object scale, texture resolution and UV area, and it is what tells you whether a texture will look sharp or blurry at a given distance.

#### Get Texel Density

Pick a **Map size** (64 up to 8K) and press **Get Texel**:

* If one or more faces are selected, the texel density is measured across that selection.
* If nothing is selected, a random face on the object is used instead.

The result is shown in px/m and, if "Use texel value when checking texel density" is enabled, is automatically carried over as the target for **Check Texel Density** below.

#### Set Texel Density

Enter a **Desired texel (px/m)** and press **Set Texel**. TechArt Tools measures the current texel density of the selected faces (or the whole object if nothing is selected) and scales their UVs around the median so the result matches the target. Works with a multi-object Edit Mode selection.

Increasing texel density enlarges the UV footprint — if the texture is not meant to tile, double-check afterwards that the islands still fit inside the 0–1 UV space.

#### Check Texel Density

Colors every polygon of the selected object(s) by how close its texel density is to the target:

* **Green** — in range.
* **Pink** — stretched (texel density above the target by more than the allowed margin).
* **Blue** — compressed (texel density below the target by more than the allowed margin).

**Range +/- (%)** sets the allowed tolerance (1–30%, default 10%). Example: target texel 200, range 10% → anything between 180 and 220 counts as in range.

##### Additional Checks

* **Tiny Polygons** — flags faces at or below a given world-space area (m²). Faces this small are usually invisible in the final render and can often be merged or removed.
* **Tiny UV Shells** — flags faces whose UV footprint is at or below a given size in pixels (relative to the selected Map size). There isn't enough texture space there to show any detail.

Each flagged category shows a **Select** button to jump straight to the offending faces.

##### Clean Check

Removes the check material and clears the results.

---

## Checker (QA Checklist)

Found in the **3D Viewport** sidebar. Press **Run Check** to evaluate the selected object(s) (Object Mode) against 13 checks, each shown with a colored status and, where it can be automated safely, a **Fix** button.

| # | Check | Fixable |
| :---- | :---- | :---- |
| 1 | Correct System Units — scene units should be Metric with a scale of 1.0 (meters). | Yes |
| 2 | Correct file / object / material names — flags default-looking names (Cube, Sphere, Material.001, …) and names that don't relate to the file name. | Manual |
| 3 | Pivot at the world origin [0,0,0]. | Yes |
| 4 | Pivot inside the object's bounding box. | Yes |
| 5 | No hidden objects or collections in the scene. | Yes |
| 6 | Backface culling is ON for every material. | Yes |
| 7 | No scale or rotation transform on the object (Apply Transform is expected before export). | Yes |
| 8 | Correct polygons — flags n-gons, non-planar and non-convex quads. | Yes (n-gons only — this changes topology) |
| 9 | Correct materials on the scene — flags unused materials in the file and objects with no material assigned. | Yes (assigns a default material where missing) |
| 10 | UV shells inside the [0,1] area. | Manual |
| 11 | Quantity of UV maps per object — informational, more than one is unusual for a simple model. | Manual |
| 12 | UV utilization — average and lowest coverage across the selection (50% / 75% thresholds). | Manual |
| 13 | Material slots per object — informational, more than one is unusual for a simple model. | Manual |

A short summary ("N / 13 checks passed") is shown at the top of the results.

---

## Installation

1. Download the extension package (`techart_tools.zip`).
2. In Blender: **Edit → Preferences → Get Extensions** → dropdown (top right) → **Install from Disk…**
3. Select the zip file.
4. Open a UV Editor or the 3D Viewport, press `N` for the sidebar, and look for the **TechArt Tools** tab.

Requires Blender 5.2 or newer.
