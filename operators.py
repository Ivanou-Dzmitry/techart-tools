import math
import os
import random
import textwrap

import bmesh
import bpy
import numpy as np
from mathutils import Vector
from mathutils.geometry import area_tri

CHECKER_DIR = os.path.join(os.path.dirname(__file__), "checkers")
TEXTURE_DIR = os.path.join(os.path.dirname(__file__), "textures")

TECHART_URL = "https://www.frosofco.com/other/techart-tools"
TECHART_VERSION = "0.42.0"


def wrap_text_for_region(context, text, min_chars=20):
    region = context.region
    width_px = region.width if region else 300
    ui_scale = context.preferences.system.ui_scale
    chars = max(min_chars, int(width_px / (6.5 * ui_scale)) - 6)
    return textwrap.wrap(text, width=chars)

CHECKERS = (
    ("STANDARD", "Standard", "checker_standard.tga"),
    ("DIAGONAL", "Diagonal", "checker_diagonal.tga"),
    ("DIGITAL", "Digital", "checker_digital.tga"),
    ("GRADIENT", "Gradient", "checker_gradient.tga"),
)

CHECKER_TIPS = {
    "STANDARD": (
        "Standard checker assigned. It reveals stretching and pinching on the "
        "UV layout - the most commonly used checker."
    ),
    "DIGITAL": (
        "Digital checker assigned. The numbers make it easy to spot mirrored or "
        "flipped UV islands, which matters if the texture will carry text."
    ),
    "DIAGONAL": (
        "Diagonal checker assigned. Good for checking that seams line up and "
        "UV direction is consistent across neighboring islands - mainly useful "
        "for camouflage-style textures."
    ),
    "GRADIENT": (
        "Gradient checker assigned. Shows roughly how UV space is used across "
        "the whole 0-1 tile. It does not tile itself, so the Texture Size buttons "
        "have no effect on it."
    ),
}

MAP_SIZES = (
    ("64", "64", ""),
    ("128", "128", ""),
    ("256", "256", ""),
    ("512", "512", ""),
    ("1024", "1K", ""),
    ("2048", "2K", ""),
    ("4096", "4K", ""),
    ("8192", "8K", ""),
)


def _edit_mesh_objects(context):
    objects = getattr(context, "objects_in_mode_unique_data", None)
    if not objects:
        objects = [context.edit_object]
    return [obj for obj in objects if obj.type == "MESH"]


def _selected_uv_loops(bm, uv_layer):
    return [loop for face in bm.faces for loop in face.loops if loop.uv_select_vert]


def _uv_pivot(uv_layer, loops):
    pivot = Vector((0.0, 0.0))
    for loop in loops:
        pivot += loop[uv_layer].uv
    pivot /= len(loops)
    return pivot


def _clear_uv_utilization(context):
    context.scene.pop("uvtt_uv_utilization", None)
    context.scene.pop("uvtt_uv_utilization_image", None)


def _set_tip(context, text):
    context.scene["uvtt_tip"] = text


def _texel_band_description(texel):
    if texel <= 49:
        return "very low (below ~1 px/cm)"
    if texel <= 200:
        return "fairly low (~1-2 px/cm)"
    if texel <= 350:
        return "medium"
    if texel <= 600:
        return "high"
    if texel <= 8192:
        return "very high"
    return "unrealistically high"


def _scale_uv_loops_around(uv_layer, loops, factor, pivot):
    for loop in loops:
        luv = loop[uv_layer]
        luv.uv = pivot + (luv.uv - pivot) * factor


def _scale_uv_loops(uv_layer, loops, factor):
    pivot = _uv_pivot(uv_layer, loops)
    _scale_uv_loops_around(uv_layer, loops, factor, pivot)


def _uv_islands_from_faces(faces, uv_layer):
    """Group faces into UV-continuous clusters (islands), restricted to `faces`."""
    face_set = set(faces)
    visited = set()
    islands = []

    def edge_uv_key(face, edge):
        for loop in face.loops:
            if loop.edge == edge:
                a = loop[uv_layer].uv
                b = loop.link_loop_next[uv_layer].uv
                return frozenset(
                    {(round(a.x, 5), round(a.y, 5)), (round(b.x, 5), round(b.y, 5))}
                )
        return None

    for start_face in faces:
        if start_face.index in visited:
            continue
        stack = [start_face]
        visited.add(start_face.index)
        island = [start_face]
        while stack:
            face = stack.pop()
            for edge in face.edges:
                linked = [f for f in edge.link_faces if f in face_set]
                if len(linked) != 2:
                    continue
                other = linked[0] if linked[1] == face else linked[1]
                if other.index in visited:
                    continue
                if edge_uv_key(face, edge) == edge_uv_key(other, edge):
                    visited.add(other.index)
                    stack.append(other)
                    island.append(other)
        islands.append(island)

    return islands


UV_ANCHORS = (
    ("SELECTION", "Selection", "Pivot around the median of the scaled UVs"),
    ("UV_CENTER", "Center", "Pivot at the center of the 0-1 UV tile"),
    ("UV_LEFT_BOTTOM", "Left Bottom", "Pivot at the [0,0] corner of the UV tile"),
    ("UV_LEFT_TOP", "Left Top", "Pivot at the [0,1] corner of the UV tile"),
    ("UV_RIGHT_BOTTOM", "Right Bottom", "Pivot at the [1,0] corner of the UV tile"),
    ("UV_RIGHT_TOP", "Right Top", "Pivot at the [1,1] corner of the UV tile"),
    ("CURSOR_2D", "2D Cursor", "Pivot at the UV Editor's 2D cursor position"),
)


def _uv_anchor_point(context, anchor, uv_layer, loops):
    if anchor == "UV_CENTER":
        return Vector((0.5, 0.5))
    if anchor == "UV_LEFT_BOTTOM":
        return Vector((0.0, 0.0))
    if anchor == "UV_LEFT_TOP":
        return Vector((0.0, 1.0))
    if anchor == "UV_RIGHT_BOTTOM":
        return Vector((1.0, 0.0))
    if anchor == "UV_RIGHT_TOP":
        return Vector((1.0, 1.0))
    if anchor == "CURSOR_2D":
        space = context.space_data
        if space is not None and space.type == "IMAGE_EDITOR":
            return Vector(space.cursor_location)
        return Vector((0.5, 0.5))
    return _uv_pivot(uv_layer, loops)


def _bmesh_for_object(context, obj):
    if obj.mode == "EDIT" and obj.data.is_editmode:
        return bmesh.from_edit_mesh(obj.data), False
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    return bm, True


def _tri_area_2d(a, b, c):
    return abs((b.x - a.x) * (c.y - a.y) - (c.x - a.x) * (b.y - a.y)) / 2.0


def _face_uv_area(face, uv_layer):
    coords = [loop[uv_layer].uv for loop in face.loops]
    if len(coords) < 3:
        return 0.0
    total = 0.0
    for i in range(1, len(coords) - 1):
        total += _tri_area_2d(coords[0], coords[i], coords[i + 1])
    return total


def _face_uv_bounds(face, uv_layer):
    coords = [loop[uv_layer].uv for loop in face.loops]
    xs = [c.x for c in coords]
    ys = [c.y for c in coords]
    return (max(xs) - min(xs)), (max(ys) - min(ys))


def _world_face_area(face, matrix_world):
    verts = [matrix_world @ v.co for v in face.verts]
    if len(verts) < 3:
        return 0.0
    total = 0.0
    for i in range(1, len(verts) - 1):
        total += area_tri(verts[0], verts[i], verts[i + 1])
    return total


class UVTT_OT_hello(bpy.types.Operator):
    """Sanity-check operator, confirms the addon is registered and running"""

    bl_idname = "uvtt.hello"
    bl_label = "UV Tech Tools: Hello"
    bl_options = {"REGISTER"}

    def execute(self, context):
        self.report({"INFO"}, "UV Tech Tools is installed and working")
        return {"FINISHED"}


class UVTT_OT_rotate(bpy.types.Operator):
    """Rotate selected UVs by a fixed angle around their median point"""

    bl_idname = "uvtt.rotate"
    bl_label = "Rotate UV"
    bl_options = {"REGISTER", "UNDO"}

    angle: bpy.props.FloatProperty(
        name="Angle",
        description="Rotation angle in degrees, positive = counter-clockwise",
        default=90.0,
        subtype="ANGLE",
    )

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context):
        angle = math.radians(self.angle)
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)

        any_selected = False

        for obj in _edit_mesh_objects(context):
            bm = bmesh.from_edit_mesh(obj.data)
            uv_layer = bm.loops.layers.uv.active
            if uv_layer is None:
                continue

            loops = _selected_uv_loops(bm, uv_layer)
            if not loops:
                continue

            any_selected = True
            pivot = _uv_pivot(uv_layer, loops)

            for loop in loops:
                luv = loop[uv_layer]
                x = luv.uv.x - pivot.x
                y = luv.uv.y - pivot.y
                luv.uv = (
                    pivot.x + x * cos_a - y * sin_a,
                    pivot.y + x * sin_a + y * cos_a,
                )

            bmesh.update_edit_mesh(obj.data)

        if not any_selected:
            self.report({"WARNING"}, "No UVs selected")
            return {"CANCELLED"}

        _clear_uv_utilization(context)
        _set_tip(
            context,
            "Rotated the selected UVs around their median point. Useful for "
            "straightening seams or fixing a wrong texture orientation before packing.",
        )

        return {"FINISHED"}


class UVTT_OT_scale(bpy.types.Operator):
    """Scale selected UVs by a fixed factor around their median point"""

    bl_idname = "uvtt.scale"
    bl_label = "Scale UV"
    bl_options = {"REGISTER", "UNDO"}

    factor: bpy.props.FloatProperty(
        name="Factor",
        description="Scale multiplier applied to the selected UVs",
        default=2.0,
    )

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context):
        any_selected = False

        for obj in _edit_mesh_objects(context):
            bm = bmesh.from_edit_mesh(obj.data)
            uv_layer = bm.loops.layers.uv.active
            if uv_layer is None:
                continue

            loops = _selected_uv_loops(bm, uv_layer)
            if not loops:
                continue

            any_selected = True
            _scale_uv_loops(uv_layer, loops, self.factor)

            bmesh.update_edit_mesh(obj.data)

        if not any_selected:
            self.report({"WARNING"}, "No UVs selected")
            return {"CANCELLED"}

        _clear_uv_utilization(context)
        _set_tip(
            context,
            "Scaled the selected UVs around their median point. Handy for quickly "
            "balancing texel density between UV islands without leaving Edit Mode.",
        )

        return {"FINISHED"}


class UVTT_OT_move(bpy.types.Operator):
    """Move selected UVs by a fixed offset along U or V"""

    bl_idname = "uvtt.move"
    bl_label = "Move UV"
    bl_options = {"REGISTER", "UNDO"}

    offset_u: bpy.props.FloatProperty(name="U", default=0.0, options={"SKIP_SAVE"})
    offset_v: bpy.props.FloatProperty(name="V", default=0.0, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context):
        any_selected = False

        for obj in _edit_mesh_objects(context):
            bm = bmesh.from_edit_mesh(obj.data)
            uv_layer = bm.loops.layers.uv.active
            if uv_layer is None:
                continue

            loops = _selected_uv_loops(bm, uv_layer)
            if not loops:
                continue

            any_selected = True

            for loop in loops:
                luv = loop[uv_layer]
                luv.uv = (luv.uv.x + self.offset_u, luv.uv.y + self.offset_v)

            bmesh.update_edit_mesh(obj.data)

        if not any_selected:
            self.report({"WARNING"}, "No UVs selected")
            return {"CANCELLED"}

        _clear_uv_utilization(context)
        _set_tip(
            context,
            "Shifted the UVs by one full tile along U or V. Useful for tiling "
            "textures or moving an island onto a neighboring UV tile.",
        )

        return {"FINISHED"}


class UVTT_OT_align(bpy.types.Operator):
    """Align selected UVs to a common X or Y coordinate (median of the selection)"""

    bl_idname = "uvtt.align"
    bl_label = "Align UV"
    bl_options = {"REGISTER", "UNDO"}

    axis: bpy.props.EnumProperty(
        name="Axis",
        items=(
            ("X", "Vertical", "Align to a common X coordinate"),
            ("Y", "Horizontal", "Align to a common Y coordinate"),
        ),
        default="X",
    )

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context):
        any_selected = False

        for obj in _edit_mesh_objects(context):
            bm = bmesh.from_edit_mesh(obj.data)
            uv_layer = bm.loops.layers.uv.active
            if uv_layer is None:
                continue

            loops = _selected_uv_loops(bm, uv_layer)
            if not loops:
                continue

            any_selected = True
            pivot = _uv_pivot(uv_layer, loops)

            for loop in loops:
                luv = loop[uv_layer]
                if self.axis == "X":
                    luv.uv = (pivot.x, luv.uv.y)
                else:
                    luv.uv = (luv.uv.x, pivot.y)

            bmesh.update_edit_mesh(obj.data)

        if not any_selected:
            self.report({"WARNING"}, "No UVs selected")
            return {"CANCELLED"}

        _clear_uv_utilization(context)
        _set_tip(
            context,
            "Aligned the selected UVs into a single row or column. Useful for "
            "straightening tileable trims and repeating patterns.",
        )

        return {"FINISHED"}


class UVTT_OT_flip(bpy.types.Operator):
    """Mirror selected UVs along U or V around their median point"""

    bl_idname = "uvtt.flip"
    bl_label = "Flip UV"
    bl_options = {"REGISTER", "UNDO"}

    axis: bpy.props.EnumProperty(
        name="Axis",
        items=(
            ("U", "U", "Mirror along U (horizontal)"),
            ("V", "V", "Mirror along V (vertical)"),
        ),
        default="U",
    )

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context):
        any_selected = False

        for obj in _edit_mesh_objects(context):
            bm = bmesh.from_edit_mesh(obj.data)
            uv_layer = bm.loops.layers.uv.active
            if uv_layer is None:
                continue

            loops = _selected_uv_loops(bm, uv_layer)
            if not loops:
                continue

            any_selected = True
            pivot = _uv_pivot(uv_layer, loops)

            for loop in loops:
                luv = loop[uv_layer]
                if self.axis == "U":
                    luv.uv = (2.0 * pivot.x - luv.uv.x, luv.uv.y)
                else:
                    luv.uv = (luv.uv.x, 2.0 * pivot.y - luv.uv.y)

            bmesh.update_edit_mesh(obj.data)

        if not any_selected:
            self.report({"WARNING"}, "No UVs selected")
            return {"CANCELLED"}

        _clear_uv_utilization(context)
        _set_tip(
            context,
            "Mirrored the selected UVs around their median point along the %s axis."
            % self.axis,
        )

        return {"FINISHED"}


class UVTT_OT_straighten(bpy.types.Operator):
    """Straighten a curved strip of quads into a straight grid.

    Wraps Blender's Follow Active Quads: picks the strip's end quad as the
    active face (the selected quad with the fewest selected neighbours,
    preferring the one whose UV edges are closest to axis-aligned, so the
    result comes out upright), unrolls the strip from it using the chosen
    spacing mode, then rotates it to the nearest vertical or horizontal
    axis so it doesn't stay tilted.
    """

    bl_idname = "uvtt.straighten"
    bl_label = "Straighten"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context):
        found = False
        strips = []

        for obj in _edit_mesh_objects(context):
            bm = bmesh.from_edit_mesh(obj.data)
            uv_layer = bm.loops.layers.uv.active
            if uv_layer is None:
                continue

            faces = [
                f for f in bm.faces if len(f.loops) == 4 and all(l.uv_select_vert for l in f.loops)
            ]
            if not faces:
                continue
            face_set = set(faces)
            bm.faces.index_update()
            strips.append((obj, [f.index for f in faces]))

            def neighbour_count(face):
                return sum(
                    1
                    for edge in face.edges
                    for other in edge.link_faces
                    if other is not face and other in face_set
                )

            def axis_misalignment(face):
                score = 0.0
                for loop in face.loops:
                    v = loop.link_loop_next[uv_layer].uv - loop[uv_layer].uv
                    if v.length_squared > 0.0:
                        score += abs(v.x * v.y) / v.length_squared
                return score

            fewest = min(neighbour_count(f) for f in faces)
            ends = [f for f in faces if neighbour_count(f) == fewest]
            bm.faces.active = min(ends, key=axis_misalignment)
            bmesh.update_edit_mesh(obj.data)
            found = True

        if not found:
            self.report({"WARNING"}, "Select a strip of quads in the UV Editor first")
            return {"CANCELLED"}

        try:
            bpy.ops.uv.follow_active_quads(mode=context.scene.uvtt_straighten_mode)
        except RuntimeError as error:
            self.report({"WARNING"}, str(error).replace("Error: ", ""))
            return {"CANCELLED"}

        for obj, indices in strips:
            bm = bmesh.from_edit_mesh(obj.data)
            uv_layer = bm.loops.layers.uv.active
            bm.faces.ensure_lookup_table()
            loops = [loop for i in indices for loop in bm.faces[i].loops]
            uvs = [loop[uv_layer].uv.copy() for loop in loops]
            center = sum(uvs, Vector((0.0, 0.0))) / len(uvs)
            sxx = sum((uv.x - center.x) ** 2 for uv in uvs)
            syy = sum((uv.y - center.y) ** 2 for uv in uvs)
            sxy = sum((uv.x - center.x) * (uv.y - center.y) for uv in uvs)
            angle = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
            quarter = math.pi / 2.0
            delta = round(angle / quarter) * quarter - angle
            cos_a, sin_a = math.cos(delta), math.sin(delta)
            for loop, uv in zip(loops, uvs):
                rel = uv - center
                loop[uv_layer].uv = center + Vector(
                    (rel.x * cos_a - rel.y * sin_a, rel.x * sin_a + rel.y * cos_a)
                )
            bmesh.update_edit_mesh(obj.data)

        _clear_uv_utilization(context)
        _set_tip(
            context,
            "Straightened the selected strip of quads by unrolling it from its end "
            "quad (Follow Active Quads), then rotated it to the nearest vertical or "
            "horizontal axis. It needs a connected strip or grid of quads.",
        )

        return {"FINISHED"}


class UVTT_OT_relax(bpy.types.Operator):
    """Relax the selected UVs to reduce stretching.

    Wraps Blender's UV > Minimize Stretch, run for a fixed number of
    iterations instead of interactively, so it's one click. Works on the
    selected UVs; pinned UVs stay put.
    """

    bl_idname = "uvtt.relax"
    bl_label = "Relax"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context):
        try:
            bpy.ops.uv.minimize_stretch(iterations=context.scene.uvtt_relax_iterations)
        except RuntimeError as error:
            self.report({"WARNING"}, str(error).replace("Error: ", ""))
            return {"CANCELLED"}

        _clear_uv_utilization(context)
        _set_tip(
            context,
            "Relaxed the selected UVs for %d iteration(s) (Minimize Stretch) to "
            "reduce stretching. Pin any UVs you want to keep in place first."
            % context.scene.uvtt_relax_iterations,
        )

        return {"FINISHED"}


class UVTT_OT_auto_uv(bpy.types.Operator):
    """Cube-project the whole mesh and pack the resulting UV islands.

    Applies rotation and scale on the object(s) first (Cube Projection uses
    local axes, so "up" only stays up if local axes already match World),
    then cube-projects and runs Pack Islands with Rotate off and Scale on,
    so islands keep a consistent, world-aligned orientation instead of the
    arbitrary rotation a Smart UV-style unwrap can pick.
    """

    bl_idname = "uvtt.auto_uv"
    bl_label = "Unwrap"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context):
        margin = context.scene.uvtt_auto_uv_margin
        objects = _edit_mesh_objects(context)

        bpy.ops.object.mode_set(mode="OBJECT")
        with context.temp_override(
            active_object=objects[0],
            selected_editable_objects=objects,
            selected_objects=objects,
            object=objects[0],
        ):
            bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
        bpy.ops.object.mode_set(mode="EDIT")

        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.cube_project()
        bpy.ops.uv.pack_islands(rotate=False, scale=True, margin=margin)

        shells = 0
        for obj in objects:
            bm = bmesh.from_edit_mesh(obj.data)
            uv_layer = bm.loops.layers.uv.active
            if uv_layer is None:
                continue
            shells += len(_uv_islands_from_faces(list(bm.faces), uv_layer))
        context.scene.uvtt_auto_uv_last_shells = shells

        _clear_uv_utilization(context)
        _set_tip(
            context,
            "Applied rotation/scale, then cube-projected and packed the UV layout "
            "(Rotate off, Scale on), so islands keep their world-aligned "
            "orientation instead of being rotated arbitrarily.",
        )

        return {"FINISHED"}


def _uv_island_bbox(island, uv_layer):
    xs = []
    ys = []
    for face in island:
        for loop in face.loops:
            uv = loop[uv_layer].uv
            xs.append(uv.x)
            ys.append(uv.y)
    return min(xs), min(ys), max(xs), max(ys)


def _dims_similar(a, b, tolerance):
    for x, y in zip(a, b):
        if abs(x - y) > max(x, y) * tolerance:
            return False
    return True


def _translate_island(island, uv_layer, offset):
    for face in island:
        for loop in face.loops:
            loop[uv_layer].uv += offset


def _shelf_pack(uv_layer, entries, margin, start=(0.0, 0.0)):
    """Translate each entry's island(s) into a left-to-right row layout,
    starting at `start` (U, V) and wrapping back to that same U once the
    next entry would cross the UV tile's right edge (U=1). Each entry is a
    dict with "islands" (a list of islands moved together as one rigid
    group), "x0", "y0" (its current bounding-box origin) and
    "width"/"height". Sorts tallest-first so rows come out reasonably tidy.
    """

    start_u, start_v = start
    cursor_u = start_u
    cursor_v = start_v
    shelf_height = 0.0

    for entry in sorted(entries, key=lambda e: e["height"], reverse=True):
        width, height = entry["width"], entry["height"]
        if cursor_u > start_u and cursor_u + width > 1.0:
            cursor_u = start_u
            cursor_v += shelf_height + margin
            shelf_height = 0.0

        offset = Vector((cursor_u - entry["x0"], cursor_v - entry["y0"]))
        for island in entry["islands"]:
            _translate_island(island, uv_layer, offset)

        cursor_u += width + margin
        shelf_height = max(shelf_height, height)


class UVTT_OT_stack_similar(bpy.types.Operator):
    """Find UV islands with a similar bounding-box size, stack them, and
    arrange the result into an orderly grid.

    Islands are grouped by (width, height), matched within the Range
    tolerance regardless of orientation - a duplicate rotated 90 degrees
    still counts as similar. Every island in a group is moved - translated
    only, never scaled or rotated - onto the first island found in that
    group. The resulting distinct shapes (stacked groups and any islands
    left on their own) are then laid out left to right in a row, each
    separated by Layout Margin, wrapping to a new row once the next one
    would cross the UV tile's right edge. Works on the current face
    selection, or the whole mesh if nothing is selected.
    """

    bl_idname = "uvtt.stack_similar"
    bl_label = "To Stack"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context):
        tolerance = context.scene.uvtt_stack_range / 100.0
        layout_margin = context.scene.uvtt_stack_layout_margin
        stacked = 0
        groups_used = 0
        placed = 0

        for obj in _edit_mesh_objects(context):
            bm = bmesh.from_edit_mesh(obj.data)
            uv_layer = bm.loops.layers.uv.active
            if uv_layer is None:
                continue

            faces = [f for f in bm.faces if f.select] or list(bm.faces)
            if not faces:
                continue

            islands = _uv_islands_from_faces(faces, uv_layer)
            if not islands:
                continue

            entries = [
                {"island": island, "bbox": _uv_island_bbox(island, uv_layer)}
                for island in islands
            ]

            used = [False] * len(entries)
            stacks = []
            for i, entry in enumerate(entries):
                if used[i]:
                    continue
                x0, y0, x1, y1 = entry["bbox"]
                dims_i = tuple(sorted((x1 - x0, y1 - y0)))
                group = [i]
                for j in range(i + 1, len(entries)):
                    if used[j]:
                        continue
                    jx0, jy0, jx1, jy1 = entries[j]["bbox"]
                    dims_j = tuple(sorted((jx1 - jx0, jy1 - jy0)))
                    if _dims_similar(dims_i, dims_j, tolerance):
                        group.append(j)
                for idx in group:
                    used[idx] = True
                stacks.append(group)

            stack_entries = []
            for group in stacks:
                anchor = entries[group[0]]
                ax0, ay0, ax1, ay1 = anchor["bbox"]
                anchor_center = Vector(((ax0 + ax1) / 2.0, (ay0 + ay1) / 2.0))
                islands_here = [anchor["island"]]

                for idx in group[1:]:
                    member = entries[idx]
                    mx0, my0, mx1, my1 = member["bbox"]
                    member_center = Vector(((mx0 + mx1) / 2.0, (my0 + my1) / 2.0))
                    _translate_island(member["island"], uv_layer, anchor_center - member_center)
                    islands_here.append(member["island"])
                    stacked += 1

                if len(group) > 1:
                    groups_used += 1

                stack_entries.append(
                    {
                        "islands": islands_here,
                        "x0": ax0,
                        "y0": ay0,
                        "width": ax1 - ax0,
                        "height": ay1 - ay0,
                    }
                )

            _shelf_pack(uv_layer, stack_entries, layout_margin)
            placed += len(stack_entries)

            bmesh.update_edit_mesh(obj.data)

        if placed == 0:
            context.scene.uvtt_stack_last_count = 0
            self.report({"WARNING"}, "No UV islands found to stack")
            return {"CANCELLED"}

        context.scene.uvtt_stack_last_count = groups_used

        _clear_uv_utilization(context)
        self.report(
            {"INFO"},
            "Stacked %d island(s) into %d group(s), arranged %d shape(s) into rows"
            % (stacked, groups_used, placed),
        )
        _set_tip(
            context,
            "Stacked %d UV island(s) into %d matching group(s) (within a %d%% size "
            "tolerance) and arranged the %d resulting shape(s) into rows across the "
            "UV tile." % (stacked, groups_used, context.scene.uvtt_stack_range, placed),
        )

        return {"FINISHED"}


class UVTT_OT_count_stack_elements(bpy.types.Operator):
    """Count the UV islands in the current face selection - the "stack" that
    Divide will split apart. Works on the current face selection only."""

    bl_idname = "uvtt.count_stack_elements"
    bl_label = "Element Count"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context):
        count = 0
        for obj in _edit_mesh_objects(context):
            bm = bmesh.from_edit_mesh(obj.data)
            uv_layer = bm.loops.layers.uv.active
            if uv_layer is None:
                continue
            faces = [f for f in bm.faces if f.select]
            if not faces:
                continue
            count += len(_uv_islands_from_faces(faces, uv_layer))

        context.scene.uvtt_stackdist_count = count
        if count == 0:
            self.report({"WARNING"}, "Select a stack of overlapping UV islands first")
            return {"CANCELLED"}

        self.report({"INFO"}, "Elements: %d" % count)
        return {"FINISHED"}


class UVTT_OT_divide_stack(bpy.types.Operator):
    """Split the selected stack of UV islands into evenly-sized groups and
    arrange those groups into rows, in place.

    Splits the islands in the current face selection into "Divide to"
    groups (in original order - any remainder goes to the last group), each
    group kept as one rigid unit (its islands stay stacked/overlapping the
    way they were), then shelf-packs the groups left to right starting from
    the stack's own current position - not the UV tile's origin - wrapping
    to a new row once the next group would cross the tile's right edge.
    Works on the current face selection only.
    """

    bl_idname = "uvtt.divide_stack"
    bl_label = "Divide"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context):
        divide_to = context.scene.uvtt_stackdist_divide_to
        margin = context.scene.uvtt_stackdist_margin
        group_count = 0

        for obj in _edit_mesh_objects(context):
            bm = bmesh.from_edit_mesh(obj.data)
            uv_layer = bm.loops.layers.uv.active
            if uv_layer is None:
                continue

            faces = [f for f in bm.faces if f.select]
            if not faces:
                continue

            islands = _uv_islands_from_faces(faces, uv_layer)
            if not islands:
                continue

            stack_xs = []
            stack_ys = []
            for island in islands:
                x0, y0, x1, y1 = _uv_island_bbox(island, uv_layer)
                stack_xs.extend((x0, x1))
                stack_ys.extend((y0, y1))
            start = (min(stack_xs), min(stack_ys))

            n = max(1, min(divide_to, len(islands)))
            base = len(islands) // n
            remainder = len(islands) % n

            entries = []
            index = 0
            for g in range(n):
                size = base + (remainder if g == n - 1 else 0)
                if size == 0:
                    continue
                group_islands = islands[index : index + size]
                index += size

                xs = []
                ys = []
                for island in group_islands:
                    x0, y0, x1, y1 = _uv_island_bbox(island, uv_layer)
                    xs.extend((x0, x1))
                    ys.extend((y0, y1))
                entries.append(
                    {
                        "islands": group_islands,
                        "x0": min(xs),
                        "y0": min(ys),
                        "width": max(xs) - min(xs),
                        "height": max(ys) - min(ys),
                    }
                )

            _shelf_pack(uv_layer, entries, margin, start=start)
            group_count += len(entries)

            bmesh.update_edit_mesh(obj.data)

        if group_count == 0:
            self.report({"WARNING"}, "Select a stack of overlapping UV islands first")
            return {"CANCELLED"}

        _clear_uv_utilization(context)
        self.report({"INFO"}, "Divided the stack into %d group(s)" % group_count)
        _set_tip(
            context,
            "Divided the selected stack into %d group(s) and arranged them into "
            "rows across the UV tile." % group_count,
        )

        return {"FINISHED"}


def _set_material_preview_shading(context):
    screen = context.screen
    if screen is None:
        return
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        for space in area.spaces:
            if space.type == "VIEW_3D":
                space.shading.type = "MATERIAL"


def _remember_original_material(obj):
    current = obj.data.materials[0] if obj.data.materials else None
    is_ours = current is not None and current.get("uvtt_generated")
    if not obj.uvtt_material_saved and not is_ours:
        obj.uvtt_original_material = current
        obj.uvtt_material_saved = True


def _assign_material_to_selection(context, mat):
    objects = context.selected_objects or [context.edit_object]
    applied = False
    for obj in objects:
        if obj is None or obj.type != "MESH":
            continue
        _remember_original_material(obj)
        if obj.data.materials:
            obj.data.materials[0] = mat
        else:
            obj.data.materials.append(mat)
        applied = True
    return applied


def _load_checker_image(filename):
    filepath = os.path.join(CHECKER_DIR, filename)
    return bpy.data.images.load(filepath, check_existing=True)


def _ensure_link(links, from_socket, to_socket):
    for link in links:
        if link.from_socket == from_socket and link.to_socket == to_socket:
            return
    links.new(from_socket, to_socket)


def _checker_material(image, tileable):
    mat_name = "UVTT_" + image.name
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        mat = bpy.data.materials.new(mat_name)
        mat.use_nodes = True

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")

    uv_node = nodes.get("UVTT_UVMap")
    if uv_node is None:
        uv_node = nodes.new("ShaderNodeUVMap")
        uv_node.name = "UVTT_UVMap"

    mapping = nodes.get("UVTT_Mapping")
    if mapping is None:
        mapping = nodes.new("ShaderNodeMapping")
        mapping.name = "UVTT_Mapping"

    tex_node = nodes.get("UVTT_Image")
    if tex_node is None:
        tex_node = nodes.new("ShaderNodeTexImage")
        tex_node.name = "UVTT_Image"
    tex_node.image = image

    _ensure_link(links, uv_node.outputs["UV"], mapping.inputs["Vector"])
    _ensure_link(links, mapping.outputs["Vector"], tex_node.inputs["Vector"])
    if bsdf is not None:
        _ensure_link(links, tex_node.outputs["Color"], bsdf.inputs["Base Color"])

    mat["uvtt_generated"] = True
    mat["uvtt_checker"] = True
    mat["uvtt_tileable"] = tileable
    return mat


class UVTT_OT_set_checker(bpy.types.Operator):
    """Assign a checker texture to the selected objects for visual UV inspection"""

    bl_idname = "uvtt.set_checker"
    bl_label = "Set Checker"
    bl_options = {"REGISTER", "UNDO"}

    checker: bpy.props.EnumProperty(
        name="Checker",
        items=tuple((cid, label, "") for cid, label, _ in CHECKERS),
    )

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects) or context.edit_object is not None

    def execute(self, context):
        filename = next(f for cid, _, f in CHECKERS if cid == self.checker)

        try:
            image = _load_checker_image(filename)
        except RuntimeError as ex:
            self.report({"ERROR"}, "Could not load checker image: %s" % ex)
            return {"CANCELLED"}

        mat = _checker_material(image, tileable=self.checker != "GRADIENT")
        applied = _assign_material_to_selection(context, mat)

        if context.area is not None and context.area.type == "IMAGE_EDITOR":
            context.area.spaces.active.image = image

        if not applied:
            self.report({"WARNING"}, "No mesh objects selected")
            return {"CANCELLED"}

        _set_material_preview_shading(context)
        _clear_uv_utilization(context)
        _set_tip(context, CHECKER_TIPS[self.checker])

        return {"FINISHED"}


def _safe_filename(name):
    invalid = '<>:"/\\|?*'
    cleaned = "".join("_" if c in invalid else c for c in name).strip()
    return cleaned or "export"


class UVTT_OT_render_uv(bpy.types.Operator):
    """Export the object's UV layout as a PNG next to the .blend file, and
    apply it to the object as a diffuse texture."""

    bl_idname = "uvtt.render_uv"
    bl_label = "Render UV"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context):
        if not bpy.data.filepath:
            self.report(
                {"WARNING"}, "Save the .blend file first - the output path is derived from it"
            )
            return {"CANCELLED"}
        if bpy.data.is_dirty:
            bpy.ops.wm.save_mainfile()

        obj = context.edit_object
        size = int(context.scene.uvtt_render_uv_size)
        opacity = context.scene.uvtt_render_uv_opacity
        directory = os.path.dirname(bpy.data.filepath)
        filepath = os.path.join(directory, _safe_filename(obj.name) + "_uv.png")

        result = bpy.ops.uv.export_layout(
            filepath=filepath,
            check_existing=False,
            export_all=True,
            mode="PNG",
            size=(size, size),
            opacity=opacity,
        )
        if "FINISHED" not in result:
            self.report({"ERROR"}, "Could not export UV layout")
            return {"CANCELLED"}

        image_name = os.path.basename(filepath)
        image = bpy.data.images.get(image_name)
        if image is not None:
            image.filepath = filepath
            image.source = "FILE"
            image.reload()
        else:
            image = bpy.data.images.load(filepath, check_existing=True)

        mat = _checker_material(image, tileable=False)
        _assign_material_to_selection(context, mat)

        _set_material_preview_shading(context)
        _clear_uv_utilization(context)
        _set_tip(
            context,
            "Exported the current UV layout to %s and applied it onto the object as "
            "a texture, so you can spot missing or broken UVs without opening the "
            "UV Editor." % filepath,
        )

        return {"FINISHED"}


class UVTT_OT_export_uv_layout(bpy.types.Operator):
    """Export the object's current UV set layout as a PNG next to the .blend file"""

    bl_idname = "uvtt.export_uv_layout"
    bl_label = "Export UV"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return (
            context.edit_object is not None
            and context.edit_object.type == "MESH"
            and bool(context.edit_object.data.uv_layers)
        )

    def execute(self, context):
        if not bpy.data.filepath:
            self.report(
                {"WARNING"}, "Save the .blend file first - the output path is derived from it"
            )
            return {"CANCELLED"}
        if bpy.data.is_dirty:
            bpy.ops.wm.save_mainfile()

        obj = context.edit_object
        size = int(context.scene.uvtt_export_uv_size)
        uv_index = obj.data.uv_layers.active_index + 1
        directory = os.path.dirname(bpy.data.filepath)
        filepath = os.path.join(
            directory, "%s_uv%d.png" % (_safe_filename(obj.name), uv_index)
        )

        result = bpy.ops.uv.export_layout(
            filepath=filepath,
            check_existing=False,
            export_all=True,
            mode="PNG",
            size=(size, size),
            opacity=0.25,
        )
        if "FINISHED" not in result:
            self.report({"ERROR"}, "Could not export UV layout")
            return {"CANCELLED"}

        self.report({"INFO"}, "Exported UV layout to %s" % filepath)
        return {"FINISHED"}


class UVTT_OT_set_checker_size(bpy.types.Operator):
    """Retile a checker material to simulate a given texture resolution"""

    bl_idname = "uvtt.set_checker_size"
    bl_label = "Checker Texture Size"
    bl_options = {"REGISTER", "UNDO"}

    size: bpy.props.IntProperty(
        name="Size",
        description="Simulated texture resolution in pixels",
        default=1024,
    )

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects) or context.edit_object is not None

    def execute(self, context):
        objects = context.selected_objects or [context.edit_object]

        applied = False
        skipped_gradient = False
        editor_source = None
        editor_repeat = 1.0

        for obj in objects:
            if obj.type != "MESH":
                continue

            for mat in obj.data.materials:
                if mat is None or not mat.get("uvtt_checker"):
                    continue

                if not mat.get("uvtt_tileable", True):
                    skipped_gradient = True
                    continue

                mapping = mat.node_tree.nodes.get("UVTT_Mapping")
                tex_node = mat.node_tree.nodes.get("UVTT_Image")
                if mapping is None or tex_node is None or tex_node.image is None:
                    continue

                base_size = max(tex_node.image.size[0], 1)
                repeat = self.size / base_size
                mapping.inputs["Scale"].default_value = (repeat, repeat, 1.0)
                editor_source = tex_node.image
                editor_repeat = repeat
                applied = True

        if not applied:
            if skipped_gradient:
                self.report({"INFO"}, "Gradient checker does not support texture size")
            else:
                self.report({"WARNING"}, "Assign a checker first")
            return {"CANCELLED"}

        _tile_checker_in_editors(context, editor_source, editor_repeat)
        _clear_uv_utilization(context)
        _set_tip(
            context,
            "Retiled the checker to simulate a %d px texture. This affects every "
            "tileable checker material in the scene, not just the active object."
            % self.size,
        )

        return {"FINISHED"}


def _solid_check_material(name, base_color, roughness):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True

    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = base_color
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = 0.0

    mat["uvtt_generated"] = True
    return mat


NORMAL_CHECK_TILING = 6.0


def _normal_check_material(image):
    mat_name = "UVTT_NormalCheck"
    mat = bpy.data.materials.get(mat_name)
    is_new = mat is None
    if mat is None:
        mat = bpy.data.materials.new(mat_name)
        mat.use_nodes = True

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (0.6, 0.6, 0.6, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.4

    uv_node = nodes.get("UVTT_UVMap")
    if uv_node is None:
        uv_node = nodes.new("ShaderNodeUVMap")
        uv_node.name = "UVTT_UVMap"

    mapping = nodes.get("UVTT_Mapping")
    if mapping is None:
        mapping = nodes.new("ShaderNodeMapping")
        mapping.name = "UVTT_Mapping"
    if is_new:
        mapping.inputs["Scale"].default_value = (
            NORMAL_CHECK_TILING,
            NORMAL_CHECK_TILING,
            1.0,
        )

    tex_node = nodes.get("UVTT_NormalImage")
    if tex_node is None:
        tex_node = nodes.new("ShaderNodeTexImage")
        tex_node.name = "UVTT_NormalImage"
    tex_node.image = image
    image.colorspace_settings.name = "Non-Color"

    normal_map_node = nodes.get("UVTT_NormalMap")
    if normal_map_node is None:
        normal_map_node = nodes.new("ShaderNodeNormalMap")
        normal_map_node.name = "UVTT_NormalMap"

    _ensure_link(links, uv_node.outputs["UV"], mapping.inputs["Vector"])
    _ensure_link(links, mapping.outputs["Vector"], tex_node.inputs["Vector"])
    _ensure_link(links, tex_node.outputs["Color"], normal_map_node.inputs["Color"])
    if bsdf is not None:
        _ensure_link(links, normal_map_node.outputs["Normal"], bsdf.inputs["Normal"])

    mat["uvtt_generated"] = True
    return mat


class UVTT_OT_set_gloss(bpy.types.Operator):
    """Assign a glossy check material to the selected objects"""

    bl_idname = "uvtt.set_gloss"
    bl_label = "Gloss"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects) or context.edit_object is not None

    def execute(self, context):
        mat = _solid_check_material("UVTT_Gloss", (0.6, 0.6, 0.6, 1.0), roughness=0.05)

        if not _assign_material_to_selection(context, mat):
            self.report({"WARNING"}, "No mesh objects selected")
            return {"CANCELLED"}

        _set_material_preview_shading(context)
        _clear_uv_utilization(context)
        _set_tip(
            context,
            "Assigned a glossy material. Sharp specular highlights make it easier "
            "to spot faceting and other surface artifacts.",
        )

        return {"FINISHED"}


class UVTT_OT_set_matte(bpy.types.Operator):
    """Assign a matte check material to the selected objects"""

    bl_idname = "uvtt.set_matte"
    bl_label = "Matte"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects) or context.edit_object is not None

    def execute(self, context):
        mat = _solid_check_material("UVTT_Matte", (0.6, 0.6, 0.6, 1.0), roughness=1.0)

        if not _assign_material_to_selection(context, mat):
            self.report({"WARNING"}, "No mesh objects selected")
            return {"CANCELLED"}

        _set_material_preview_shading(context)
        _clear_uv_utilization(context)
        _set_tip(
            context,
            "Assigned a matte material. Neutral and easy on the eyes - good for "
            "a general shape and silhouette review.",
        )

        return {"FINISHED"}


class UVTT_OT_set_normal_check(bpy.types.Operator):
    """Assign a test normal map material to the selected objects"""

    bl_idname = "uvtt.set_normal_check"
    bl_label = "NM"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects) or context.edit_object is not None

    def execute(self, context):
        filepath = os.path.join(TEXTURE_DIR, "normal_test.png")
        try:
            image = bpy.data.images.load(filepath, check_existing=True)
        except RuntimeError as ex:
            self.report({"ERROR"}, "Could not load normal test texture: %s" % ex)
            return {"CANCELLED"}

        mat = _normal_check_material(image)

        if not _assign_material_to_selection(context, mat):
            self.report({"WARNING"}, "No mesh objects selected")
            return {"CANCELLED"}

        _set_material_preview_shading(context)
        _clear_uv_utilization(context)
        _set_tip(
            context,
            "Assigned a test normal map. Readable UP/DOWN text reveals a flipped "
            "Y channel or mirrored UVs at a glance. Replace it with your own map "
            "once you have one.",
        )

        return {"FINISHED"}


VIEW_IMAGE_PREFIX = "UVTT_view_"


def _our_image_names():
    names = {filename for _, _, filename in CHECKERS}
    for mat in bpy.data.materials:
        if not mat.get("uvtt_checker") or mat.node_tree is None:
            continue
        node = mat.node_tree.nodes.get("UVTT_Image")
        if node is not None and node.image is not None:
            names.add(node.image.name)
    return names


def _is_our_editor_image(image, ours):
    return (
        image.name in ours
        or image.name.startswith(VIEW_IMAGE_PREFIX)
        or image.name.endswith("_uv.png")
    )


def _image_editor_spaces(context):
    screen = context.screen
    if screen is None:
        return
    for area in screen.areas:
        if area.type != "IMAGE_EDITOR":
            continue
        for space in area.spaces:
            if space.type == "IMAGE_EDITOR":
                yield space


def _clear_image_editors_showing_ours(context):
    """Unset the image in any UV/Image Editor that is showing one of our
    checker, tiled-view or Render UV images, so Remove Checker clears it
    there too, and drop the generated tiled-view images."""

    ours = _our_image_names()
    for space in _image_editor_spaces(context):
        if space.image is not None and _is_our_editor_image(space.image, ours):
            space.image = None
    for image in [i for i in bpy.data.images if i.name.startswith(VIEW_IMAGE_PREFIX)]:
        bpy.data.images.remove(image)


def _tile_checker_in_editors(context, source, repeat):
    """Show the checker tiled `repeat` times in any Image Editor displaying
    one of ours, by swapping in a pre-tiled copy (downscaled so it keeps the
    original pixel size). A repeat of 1 or less shows the original."""

    ours = _our_image_names()
    spaces = [
        s for s in _image_editor_spaces(context)
        if s.image is not None and _is_our_editor_image(s.image, ours)
    ]
    if not spaces:
        return

    n = int(round(repeat))
    view_name = VIEW_IMAGE_PREFIX + source.name
    old = bpy.data.images.get(view_name)

    if n <= 1:
        target = source
    else:
        width, height = source.size
        small = source.copy()
        small.scale(max(width // n, 1), max(height // n, 1))
        w, h = small.size
        pixels = np.empty(w * h * 4, dtype=np.float32)
        small.pixels.foreach_get(pixels)
        bpy.data.images.remove(small)
        tiled = np.tile(pixels.reshape((h, w, 4)), (n, n, 1))

        for space in spaces:
            if space.image is old:
                space.image = None
        if old is not None:
            bpy.data.images.remove(old)
        target = bpy.data.images.new(view_name, width=w * n, height=h * n)
        target.colorspace_settings.name = source.colorspace_settings.name
        target.pixels.foreach_set(tiled.reshape(-1))
        target.update()

    for space in spaces:
        space.image = target
    if n <= 1 and old is not None:
        bpy.data.images.remove(old)


class UVTT_OT_reset_material(bpy.types.Operator):
    """Remove the check material from the selected objects, restoring whatever material was there before"""

    bl_idname = "uvtt.reset_material"
    bl_label = "Reset Material"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects) or context.edit_object is not None

    def execute(self, context):
        objects = context.selected_objects or [context.edit_object]

        applied = False
        for obj in objects:
            if obj.type != "MESH":
                continue

            if obj.uvtt_material_saved:
                original = obj.uvtt_original_material
                if original is not None:
                    if obj.data.materials:
                        obj.data.materials[0] = original
                    else:
                        obj.data.materials.append(original)
                else:
                    obj.data.materials.clear()
                obj.uvtt_original_material = None
                obj.uvtt_material_saved = False
            else:
                obj.data.materials.clear()

            applied = True

        if not applied:
            self.report({"WARNING"}, "No mesh objects selected")
            return {"CANCELLED"}

        _clear_image_editors_showing_ours(context)
        _clear_uv_utilization(context)
        _set_tip(context, "Removed all materials from the selected object(s).")

        return {"FINISHED"}


class UVTT_OT_check_uv_utilization(bpy.types.Operator):
    """Report what percentage of the 0-1 UV tile is covered by the mesh's UVs

    Renders the UV layout to an opaque-filled image and counts non-transparent
    pixels, so overlapping islands are not double-counted (unlike a plain
    polygon-area sum).
    """

    bl_idname = "uvtt.check_uv_utilization"
    bl_label = "UV Utilization"
    bl_options = {"REGISTER"}

    resolution: bpy.props.IntProperty(
        name="Resolution",
        description="Render resolution used to sample UV coverage",
        default=512,
        min=64,
        max=2048,
    )

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context):
        filepath = os.path.join(bpy.app.tempdir, "uvtt_uv_utilization.png")

        result = bpy.ops.uv.export_layout(
            filepath=filepath,
            check_existing=False,
            export_all=True,
            mode="PNG",
            size=(self.resolution, self.resolution),
            opacity=1.0,
        )
        if "FINISHED" not in result:
            self.report({"ERROR"}, "Could not export UV layout")
            return {"CANCELLED"}

        image_name = os.path.basename(filepath)
        image = bpy.data.images.get(image_name)
        if image is not None:
            image.filepath = filepath
            image.source = "FILE"
            image.reload()
        else:
            image = bpy.data.images.load(filepath, check_existing=True)

        pixels = np.empty(len(image.pixels), dtype=np.float32)
        image.pixels.foreach_get(pixels)
        alpha = pixels[3::4]
        covered = int(np.count_nonzero(alpha > 0.5))
        percent = covered / alpha.size * 100.0

        # Bake coverage onto a solid black background (white = covered) so the
        # preview thumbnail doesn't rely on transparency to read clearly.
        pixels[0::4] = alpha
        pixels[1::4] = alpha
        pixels[2::4] = alpha
        pixels[3::4] = 1.0
        image.pixels.foreach_set(pixels)
        image.update()

        image.preview_ensure()

        context.scene["uvtt_uv_utilization"] = percent
        context.scene["uvtt_uv_utilization_image"] = image.name
        self.report({"INFO"}, "UV utilization: %.1f%%" % percent)

        if percent < 1.0:
            band = (
                "Near-zero coverage. This usually means the UV islands are "
                "positioned outside the 0-1 tile - this check only counts what "
                "falls inside it. Check the UV Editor and move islands back in."
            )
        elif percent < 25.0:
            band = (
                "Poor coverage - most of the texture space is wasted. Also check "
                "that all UV islands are within the 0-1 tile, since anything "
                "outside it isn't counted here."
            )
        elif percent < 33.0:
            band = (
                "Low coverage - packing could be noticeably tighter. Also check "
                "that all UV islands are within the 0-1 tile, since anything "
                "outside it isn't counted here."
            )
        elif percent < 60.0:
            band = "Moderate coverage - there is still room to pack tighter."
        elif percent < 75.0:
            band = "Good coverage."
        elif percent < 90.0:
            band = "Excellent coverage."
        else:
            band = (
                "Very high coverage - make sure there is still enough padding "
                "between islands to avoid texture bleeding."
            )
        _set_tip(
            context,
            "UV coverage is %.1f%%. %s (Depends on padding, of course.)" % (percent, band),
        )

        return {"FINISHED"}


class UVTT_OT_get_texel(bpy.types.Operator):
    """Measure texel density (px/m) of the selected faces, or a random face if none are selected"""

    bl_idname = "uvtt.get_texel"
    bl_label = "Get Texel"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context):
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is None:
            self.report({"WARNING"}, "Object has no UV map")
            return {"CANCELLED"}

        faces = [f for f in bm.faces if f.select]
        random_pick = False
        if not faces:
            candidates = list(bm.faces)
            if not candidates:
                self.report({"WARNING"}, "Object has no faces")
                return {"CANCELLED"}
            faces = [random.choice(candidates)]
            random_pick = True

        map_size = int(context.scene.uvtt_map_size)

        total_uv_area = sum(_face_uv_area(f, uv_layer) for f in faces)
        total_geo_area = sum(_world_face_area(f, obj.matrix_world) for f in faces)

        if total_geo_area <= 0.0:
            self.report({"WARNING"}, "Selected geometry has zero area")
            return {"CANCELLED"}

        texel = math.ceil(math.sqrt(total_uv_area / total_geo_area) * map_size)

        if context.scene.uvtt_use_measured_texel:
            context.scene.uvtt_texel_value = texel

        if random_pick:
            self.report({"INFO"}, "Texel (random face): %d px/m" % texel)
            select_note = "measured on a random face, since nothing was selected"
        else:
            self.report({"INFO"}, "Texel: %d px/m" % texel)
            select_note = "measured on the selected face(s)"

        _set_tip(
            context,
            "Texel is %d px/m (%s). That is %s for this map size. Remember, "
            "texel depends on object scale, texture resolution and UV area."
            % (texel, select_note, _texel_band_description(texel)),
        )

        return {"FINISHED"}


class UVTT_OT_set_texel(bpy.types.Operator):
    """Scale UVs of the selected faces (or the whole object) to match the desired texel density"""

    bl_idname = "uvtt.set_texel"
    bl_label = "Set Texel"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context):
        map_size = int(context.scene.uvtt_map_size)
        desired_texel = context.scene.uvtt_desired_texel
        method = context.scene.uvtt_texel_set_method
        anchor_mode = context.scene.uvtt_texel_scale_anchor

        any_changed = False

        for obj in _edit_mesh_objects(context):
            bm = bmesh.from_edit_mesh(obj.data)
            uv_layer = bm.loops.layers.uv.active
            if uv_layer is None:
                continue

            faces = [f for f in bm.faces if f.select] or list(bm.faces)
            if not faces:
                continue

            groups = _uv_islands_from_faces(faces, uv_layer) if method == "EACH" else [faces]

            shared_anchor = None
            if anchor_mode != "SELECTION" or method == "AVERAGE":
                all_loops = [loop for face in faces for loop in face.loops]
                shared_anchor = _uv_anchor_point(context, anchor_mode, uv_layer, all_loops)

            changed_this_object = False

            for group_faces in groups:
                total_uv_area = sum(_face_uv_area(f, uv_layer) for f in group_faces)
                total_geo_area = sum(_world_face_area(f, obj.matrix_world) for f in group_faces)
                if total_geo_area <= 0.0 or total_uv_area <= 0.0:
                    continue

                current_texel = math.sqrt(total_uv_area / total_geo_area) * map_size
                if current_texel <= 0.0:
                    continue

                group_loops = [loop for face in group_faces for loop in face.loops]

                if anchor_mode == "SELECTION" and method == "EACH":
                    pivot = _uv_pivot(uv_layer, group_loops)
                else:
                    pivot = shared_anchor

                _scale_uv_loops_around(uv_layer, group_loops, desired_texel / current_texel, pivot)
                changed_this_object = True

            if changed_this_object:
                bmesh.update_edit_mesh(obj.data)
                any_changed = True

        if not any_changed:
            self.report({"WARNING"}, "Nothing to set texel on")
            return {"CANCELLED"}

        _clear_uv_utilization(context)

        self.report({"INFO"}, "Texel set to %d px/m" % int(desired_texel))
        _set_tip(
            context,
            "Texel set to %d px/m (%s). Increasing texel enlarges the UV footprint - "
            "if the texture is not meant to tile, make sure the islands still fit "
            "inside the 0-1 UV space afterwards."
            % (int(desired_texel), _texel_band_description(desired_texel)),
        )
        return {"FINISHED"}


TEXEL_CHECK_COLOR_LAYER = "UVTT_TexelCheck"
TEXEL_COLOR_IN_RANGE = (0.2, 1.0, 0.2, 1.0)
TEXEL_COLOR_STRETCHED = (1.0, 0.5, 0.5, 1.0)
TEXEL_COLOR_COMPRESSED = (0.3, 0.9, 1.0, 1.0)


def _texel_check_material():
    mat_name = "UVTT_TexelCheck"
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        mat = bpy.data.materials.new(mat_name)
        mat.use_nodes = True

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Roughness"].default_value = 1.0

    attr_node = nodes.get("UVTT_ColorAttr")
    if attr_node is None:
        attr_node = nodes.new("ShaderNodeAttribute")
        attr_node.name = "UVTT_ColorAttr"
    attr_node.attribute_type = "GEOMETRY"
    attr_node.attribute_name = TEXEL_CHECK_COLOR_LAYER

    if bsdf is not None:
        _ensure_link(links, attr_node.outputs["Color"], bsdf.inputs["Base Color"])

    mat["uvtt_generated"] = True
    return mat


class UVTT_OT_check_texel(bpy.types.Operator):
    """Color-code faces by texel density and flag tiny polygons / tiny UV shells"""

    bl_idname = "uvtt.check_texel"
    bl_label = "Check Texel"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects) or context.edit_object is not None

    def execute(self, context):
        scene = context.scene
        map_size = int(scene.uvtt_map_size)
        target_texel = scene.uvtt_texel_value
        margin = target_texel * (scene.uvtt_texel_range / 100.0)
        hi = target_texel + margin
        lo = target_texel - margin

        tiny_poly_area = scene.uvtt_tiny_poly_area
        tiny_uv_size = scene.uvtt_tiny_uv_px / map_size

        objects = [
            o
            for o in (context.selected_objects or [context.edit_object])
            if o is not None and o.type == "MESH"
        ]
        if not objects:
            self.report({"WARNING"}, "No mesh objects selected")
            return {"CANCELLED"}

        in_range = stretched = compressed = 0
        tiny_poly_count = 0
        tiny_uv_count = 0

        for obj in objects:
            bm, should_free = _bmesh_for_object(context, obj)
            uv_layer = bm.loops.layers.uv.active
            if uv_layer is None:
                if should_free:
                    bm.free()
                continue

            color_layer = bm.loops.layers.float_color.get(TEXEL_CHECK_COLOR_LAYER)
            if color_layer is None:
                color_layer = bm.loops.layers.float_color.new(TEXEL_CHECK_COLOR_LAYER)

            for face in bm.faces:
                geo_area = _world_face_area(face, obj.matrix_world)
                uv_area = _face_uv_area(face, uv_layer)
                uv_w, uv_h = _face_uv_bounds(face, uv_layer)

                if geo_area <= tiny_poly_area:
                    tiny_poly_count += 1

                if uv_w <= tiny_uv_size or uv_h <= tiny_uv_size:
                    tiny_uv_count += 1

                if geo_area <= 0.0:
                    continue

                texel = math.sqrt(uv_area / geo_area) * map_size

                if texel > hi:
                    color = TEXEL_COLOR_STRETCHED
                    stretched += 1
                elif texel < lo:
                    color = TEXEL_COLOR_COMPRESSED
                    compressed += 1
                else:
                    color = TEXEL_COLOR_IN_RANGE
                    in_range += 1

                for loop in face.loops:
                    loop[color_layer] = color

            if should_free:
                bm.to_mesh(obj.data)
                obj.data.update()
                bm.free()
            else:
                bmesh.update_edit_mesh(obj.data)

        mat = _texel_check_material()
        for obj in objects:
            _remember_original_material(obj)
            if obj.data.materials:
                obj.data.materials[0] = mat
            else:
                obj.data.materials.append(mat)

        scene["uvtt_texel_inrange"] = in_range
        scene["uvtt_texel_stretched"] = stretched
        scene["uvtt_texel_compressed"] = compressed
        scene["uvtt_tiny_poly_count"] = tiny_poly_count
        scene["uvtt_tiny_uv_count"] = tiny_uv_count

        _set_material_preview_shading(context)
        _clear_uv_utilization(context)

        self.report(
            {"INFO"},
            "Texel check: %d in-range, %d stretched, %d compressed"
            % (in_range, stretched, compressed),
        )

        tips = [
            "Checked %d face(s): %d in-range (green), %d stretched (pink), "
            "%d compressed (blue)." % (in_range + stretched + compressed, in_range, stretched, compressed)
        ]
        if stretched:
            tips.append(
                "Stretched faces have a higher texel than the target - scale "
                "their UVs down, or accept it if that was intentional."
            )
        if compressed:
            tips.append(
                "Compressed faces have a lower texel than the target - scale "
                "their UVs up if they need more texture detail."
            )
        if tiny_poly_count:
            tips.append(
                "%d tiny polygon(s) found - geometry this small is usually invisible "
                "and can often be merged or removed." % tiny_poly_count
            )
        if tiny_uv_count:
            tips.append(
                "%d face(s) have a tiny UV footprint - there isn't enough texture "
                "space there to show any detail." % tiny_uv_count
            )
        _set_tip(context, " ".join(tips))

        return {"FINISHED"}


class UVTT_OT_select_tiny_polygons(bpy.types.Operator):
    """Select faces at or below the Tiny Polygons area threshold"""

    bl_idname = "uvtt.select_tiny_polygons"
    bl_label = "Select Tiny Polygons"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context):
        threshold = context.scene.uvtt_tiny_poly_area
        found = 0

        for obj in _edit_mesh_objects(context):
            bm = bmesh.from_edit_mesh(obj.data)
            for face in bm.faces:
                is_tiny = _world_face_area(face, obj.matrix_world) <= threshold
                face.select = is_tiny
                if is_tiny:
                    found += 1
            bm.select_flush(True)
            bmesh.update_edit_mesh(obj.data)

        if found == 0:
            self.report({"INFO"}, "No tiny polygons found")
            return {"CANCELLED"}

        self.report({"INFO"}, "Selected %d tiny polygon(s)" % found)
        _set_tip(
            context,
            "Selected %d tiny polygon(s). Faces this small are usually invisible "
            "in the final render - consider merging or removing them." % found,
        )
        return {"FINISHED"}


class UVTT_OT_select_tiny_uv(bpy.types.Operator):
    """Select faces whose UV footprint is at or below the Tiny UV Shells pixel threshold"""

    bl_idname = "uvtt.select_tiny_uv"
    bl_label = "Select Tiny UV Shells"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == "MESH"

    def execute(self, context):
        map_size = int(context.scene.uvtt_map_size)
        tiny_uv_size = context.scene.uvtt_tiny_uv_px / map_size
        found = 0

        for obj in _edit_mesh_objects(context):
            bm = bmesh.from_edit_mesh(obj.data)
            uv_layer = bm.loops.layers.uv.active
            if uv_layer is None:
                continue
            for face in bm.faces:
                w, h = _face_uv_bounds(face, uv_layer)
                is_tiny = w <= tiny_uv_size or h <= tiny_uv_size
                face.select = is_tiny
                if is_tiny:
                    found += 1
            bm.select_flush(True)
            bmesh.update_edit_mesh(obj.data)

        if found == 0:
            self.report({"INFO"}, "No tiny UV shells found")
            return {"CANCELLED"}

        self.report({"INFO"}, "Selected %d face(s) with tiny UV shells" % found)
        _set_tip(
            context,
            "Selected %d face(s) with a tiny UV footprint. There isn't enough "
            "texture space there to show any detail - consider merging them into "
            "a nearby island." % found,
        )
        return {"FINISHED"}


class UVTT_OT_clean_texel_check(bpy.types.Operator):
    """Clear texel check results and remove the check material from selected objects"""

    bl_idname = "uvtt.clean_texel_check"
    bl_label = "Clean Check"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects) or context.edit_object is not None

    def execute(self, context):
        objects = context.selected_objects or [context.edit_object]
        for obj in objects:
            if obj is None or obj.type != "MESH":
                continue
            obj.data.materials.clear()

        for key in (
            "uvtt_texel_inrange",
            "uvtt_texel_stretched",
            "uvtt_texel_compressed",
            "uvtt_tiny_poly_count",
            "uvtt_tiny_uv_count",
        ):
            context.scene.pop(key, None)

        _set_tip(context, "Texel check results cleared.")

        return {"FINISHED"}


classes = (
    UVTT_OT_hello,
    UVTT_OT_rotate,
    UVTT_OT_scale,
    UVTT_OT_move,
    UVTT_OT_align,
    UVTT_OT_flip,
    UVTT_OT_straighten,
    UVTT_OT_relax,
    UVTT_OT_auto_uv,
    UVTT_OT_stack_similar,
    UVTT_OT_count_stack_elements,
    UVTT_OT_divide_stack,
    UVTT_OT_set_checker,
    UVTT_OT_render_uv,
    UVTT_OT_export_uv_layout,
    UVTT_OT_set_checker_size,
    UVTT_OT_set_gloss,
    UVTT_OT_set_matte,
    UVTT_OT_set_normal_check,
    UVTT_OT_reset_material,
    UVTT_OT_check_uv_utilization,
    UVTT_OT_get_texel,
    UVTT_OT_set_texel,
    UVTT_OT_check_texel,
    UVTT_OT_select_tiny_polygons,
    UVTT_OT_select_tiny_uv,
    UVTT_OT_clean_texel_check,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.uvtt_map_size = bpy.props.EnumProperty(
        name="Map Size",
        description="Square texture resolution used for texel density calculations",
        items=MAP_SIZES,
        default="256",
    )
    bpy.types.Scene.uvtt_texel_value = bpy.props.FloatProperty(
        name="Texel",
        description="Measured or target texel density in px/m",
        default=256.0,
        min=0.0,
    )
    bpy.types.Scene.uvtt_use_measured_texel = bpy.props.BoolProperty(
        name="Use texel value when checking texel density",
        default=True,
    )
    bpy.types.Scene.uvtt_desired_texel = bpy.props.FloatProperty(
        name="Desired texel (px/m)",
        default=400.0,
        min=1.0,
    )
    bpy.types.Scene.uvtt_texel_set_method = bpy.props.EnumProperty(
        name="Mode",
        description="Set Texel Density: scale each UV cluster to the target individually, "
        "or scale the whole selection together by its average texel density",
        items=(
            ("EACH", "Each Cluster", "Scale every UV island in the selection to the target individually"),
            ("AVERAGE", "Average", "Scale the whole selection together by its average texel density"),
        ),
        default="AVERAGE",
    )
    bpy.types.Scene.uvtt_texel_scale_anchor = bpy.props.EnumProperty(
        name="Scale Anchor",
        description="Pivot point used when scaling UVs to the target texel density",
        items=UV_ANCHORS,
        default="SELECTION",
    )
    bpy.types.Scene.uvtt_texel_range = bpy.props.IntProperty(
        name="Range +/- (%)",
        default=10,
        min=1,
        max=30,
    )
    bpy.types.Scene.uvtt_tiny_poly_area = bpy.props.FloatProperty(
        name="Tiny Polygons (m2)",
        default=0.0001,
        min=0.00001,
        max=1000.0,
        precision=5,
    )
    bpy.types.Scene.uvtt_tiny_uv_px = bpy.props.IntProperty(
        name="Tiny UV Shells (px)",
        default=1,
        min=1,
        max=9,
    )
    bpy.types.Scene.uvtt_render_uv_size = bpy.props.EnumProperty(
        name="Map Size",
        description="Resolution of the exported UV layout image",
        items=MAP_SIZES,
        default="1024",
    )
    bpy.types.Scene.uvtt_render_uv_opacity = bpy.props.FloatProperty(
        name="Fill Opacity",
        description="Opacity of the translucent face fill in the exported UV layout image",
        default=0.25,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    bpy.types.Scene.uvtt_export_uv_size = bpy.props.EnumProperty(
        name="Map Size",
        description="Resolution of the exported UV layout image",
        items=MAP_SIZES,
        default="1024",
    )
    bpy.types.Scene.uvtt_auto_uv_margin = bpy.props.FloatProperty(
        name="Margin",
        description="Margin left between packed UV islands",
        default=0.03,
        min=0.0,
        max=0.5,
        subtype="FACTOR",
    )
    bpy.types.Scene.uvtt_stack_range = bpy.props.IntProperty(
        name="Range +/- (%)",
        description="How close two UV islands' bounding-box dimensions must be to "
        "count as similar",
        default=2,
        min=0,
        max=50,
    )
    bpy.types.Scene.uvtt_stack_layout_margin = bpy.props.FloatProperty(
        name="Layout Margin",
        description="Gap left between shapes when arranging the stacked result into rows",
        default=0.02,
        min=0.0,
        max=0.5,
        subtype="FACTOR",
    )
    bpy.types.Scene.uvtt_auto_uv_last_shells = bpy.props.IntProperty(
        name="Last Run Shells",
        description="UV shell count after the last Unwrap - 0 until it has been run",
        default=0,
    )
    bpy.types.Scene.uvtt_stack_last_count = bpy.props.IntProperty(
        name="Last Run Stacks",
        description="Number of groups stacked by the last To Stack run - 0 until it "
        "has been run",
        default=0,
    )
    bpy.types.Scene.uvtt_relax_iterations = bpy.props.IntProperty(
        name="Iterations",
        description="How many Minimize Stretch iterations Relax runs",
        default=50,
        min=1,
        max=1000,
    )
    bpy.types.Scene.uvtt_straighten_mode = bpy.props.EnumProperty(
        name="Mode",
        description="How the straightened strip's quads are spaced",
        items=(
            ("LENGTH_AVERAGE", "Length Average", "Space quads by the average edge length of each loop"),
            ("LENGTH", "Length", "Space quads by each edge's own length"),
            ("EVEN", "Even", "Space all quads evenly"),
        ),
        default="LENGTH_AVERAGE",
    )
    bpy.types.Scene.uvtt_stackdist_count = bpy.props.IntProperty(
        name="Elements",
        description="UV island count in the current selection, from the last Element "
        "Count run - 0 until it has been run",
        default=0,
    )
    bpy.types.Scene.uvtt_stackdist_divide_to = bpy.props.IntProperty(
        name="Divide to",
        description="Number of groups to split the selected stack into",
        default=2,
        min=1,
        max=10000,
    )
    bpy.types.Scene.uvtt_stackdist_margin = bpy.props.FloatProperty(
        name="Margin",
        description="Gap left between groups when arranging the divided stack into rows",
        default=0.02,
        min=0.0,
        max=0.5,
        subtype="FACTOR",
    )

    bpy.types.Object.uvtt_original_material = bpy.props.PointerProperty(type=bpy.types.Material)
    bpy.types.Object.uvtt_material_saved = bpy.props.BoolProperty(default=False)


def unregister():
    del bpy.types.Object.uvtt_material_saved
    del bpy.types.Object.uvtt_original_material

    del bpy.types.Scene.uvtt_stackdist_margin
    del bpy.types.Scene.uvtt_stackdist_divide_to
    del bpy.types.Scene.uvtt_straighten_mode
    del bpy.types.Scene.uvtt_relax_iterations
    del bpy.types.Scene.uvtt_stackdist_count
    del bpy.types.Scene.uvtt_stack_last_count
    del bpy.types.Scene.uvtt_auto_uv_last_shells
    del bpy.types.Scene.uvtt_stack_layout_margin
    del bpy.types.Scene.uvtt_stack_range
    del bpy.types.Scene.uvtt_auto_uv_margin
    del bpy.types.Scene.uvtt_export_uv_size
    del bpy.types.Scene.uvtt_render_uv_opacity
    del bpy.types.Scene.uvtt_render_uv_size
    del bpy.types.Scene.uvtt_tiny_uv_px
    del bpy.types.Scene.uvtt_tiny_poly_area
    del bpy.types.Scene.uvtt_texel_range
    del bpy.types.Scene.uvtt_texel_scale_anchor
    del bpy.types.Scene.uvtt_texel_set_method
    del bpy.types.Scene.uvtt_desired_texel
    del bpy.types.Scene.uvtt_use_measured_texel
    del bpy.types.Scene.uvtt_texel_value
    del bpy.types.Scene.uvtt_map_size

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
