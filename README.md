# UV Tech Tools

Blender 5.2 addon (Extensions format) for technical UV unwrapping workflows.

## Install (development)

1. Run `build.ps1` in PowerShell — it produces `dist/uv_tech_tools.zip`.
2. In Blender: Edit > Preferences > Get Extensions > dropdown (top right) > Install from Disk...
3. Select `dist/uv_tech_tools.zip`.
4. Open a UV Editor, press `N` for the sidebar, find the "UV Tech Tools" tab.

## Structure

- `blender_manifest.toml` — extension manifest (id, version, Blender compatibility, license).
- `__init__.py` — registers/unregisters submodules.
- `operators.py` — operators (`bpy.types.Operator` subclasses).
- `ui.py` — panels shown in the UV Editor sidebar.

## Status

Skeleton only — `UVTT_OT_hello` is a placeholder confirming the addon loads correctly.
