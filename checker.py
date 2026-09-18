import datetime
import os

import blf
import bmesh
import bpy
import gpu
import numpy as np
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from . import operators

LOG_TEXT_NAME = "TechArt_Checker_Log"

CHECKER_INTRO_TIP = (
    "The Checker runs a set of objective checks on the selected object(s) and tells "
    "you whether the model is ready for feedback or final review. Select one or more "
    "mesh objects and press Run Check. A checkmark means everything is fine, an "
    "orange/red icon means something needs attention, and a blue info icon is purely "
    "informational. Where a Fix button is shown, the issue can be corrected "
    "automatically - usually with a simple, safe fix. Final judgment is still yours."
)

PREPARE_INTRO_TIP = (
    "Prepare Mesh and Prepare Scene do basic, safe cleanup before a model goes for "
    "feedback: unhiding everything, clearing scale/rotation, making sure every object "
    "has a material, and switching backface culling on. Running them first means the "
    "Checker below has less to complain about."
)

class SafetyConfirmMixin:
    """Offer a Save & Continue / Cancel prompt before a bulk, hard-to-undo
    scene-modifying operation, if the file has unsaved changes.

    Unlike SaveFileMixin (used by the export operators) this never blocks the
    action - if the file was never saved there's no path to save to and the
    action doesn't need one, so it just proceeds.
    """

    def invoke(self, context, event):
        if bpy.data.filepath and bpy.data.is_dirty:
            return context.window_manager.invoke_confirm(
                self,
                event,
                title="Unsaved Changes",
                message="Save the file before continuing?",
                confirm_text="Save & Continue",
            )
        return self.execute(context)

    def _save_if_dirty(self, context):
        if bpy.data.filepath and bpy.data.is_dirty:
            bpy.ops.wm.save_mainfile()


STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_WARN = "WARN"
STATUS_INFO = "INFO"

DEFAULT_NAME_WORDS = (
    "sphere",
    "tube",
    "cylinder",
    "cone",
    "torus",
    "plane",
    "cube",
    "suzanne",
    "monkey",
    "grid",
    "text",
    "circle",
    "icosphere",
    "empty",
    "untitled",
)


def _checker_objects(context):
    objects = [o for o in context.selected_objects if o.type == "MESH"]
    if not objects and context.active_object is not None and context.active_object.type == "MESH":
        objects = [context.active_object]
    return objects


def _has_transform(obj):
    if (obj.scale - Vector((1.0, 1.0, 1.0))).length > 1e-4:
        return True
    if any(abs(a) > 1e-4 for a in obj.rotation_euler):
        return True
    return False


def check_units(objects, context):
    unit = context.scene.unit_settings
    if unit.system == "METRIC" and abs(unit.scale_length - 1.0) < 1e-6:
        return STATUS_PASS, "Scene units are Metric, scale 1.0 (meters)."
    return (
        STATUS_FAIL,
        "Scene units are %s, scale %.3f. Expected Metric with scale 1.0 (meters)."
        % (unit.system, unit.scale_length),
    )


def check_naming(objects, context):
    problems = []
    filename = ""
    if bpy.data.filepath:
        filename = os.path.splitext(os.path.basename(bpy.data.filepath))[0].lower()

    for obj in objects:
        name_lower = obj.name.lower()

        for word in DEFAULT_NAME_WORDS:
            if word in name_lower:
                problems.append("%s: default-looking name" % obj.name)
                break

        for slot in obj.material_slots:
            mat = slot.material
            if mat is not None and mat.name.lower().startswith("material"):
                problems.append("%s: default material name '%s'" % (obj.name, mat.name))

        if filename and filename not in name_lower and name_lower not in filename:
            problems.append("%s: does not match file name '%s'" % (obj.name, filename))

    if not problems:
        return STATUS_PASS, "Object and material names look intentional."
    text = "; ".join(problems[:4])
    if len(problems) > 4:
        text += " ..."
    return STATUS_WARN, text


def check_pivot_origin(objects, context):
    bad = [o.name for o in objects if o.location.length > 1e-5]
    if not bad:
        return STATUS_PASS, "Pivot is at the world origin [0,0,0]."
    return STATUS_WARN, "Pivot not at origin: %s" % ", ".join(bad)


def check_pivot_bbox(objects, context):
    bad = []
    for obj in objects:
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        xs = [c.x for c in corners]
        ys = [c.y for c in corners]
        zs = [c.z for c in corners]
        loc = obj.matrix_world.translation
        inside = (
            min(xs) <= loc.x <= max(xs)
            and min(ys) <= loc.y <= max(ys)
            and min(zs) <= loc.z <= max(zs)
        )
        if not inside:
            bad.append(obj.name)
    if not bad:
        return STATUS_PASS, "Pivot is inside the object's bounding box."
    return STATUS_WARN, "Pivot outside bounding box: %s" % ", ".join(bad)


def check_hidden(objects, context):
    hidden = [o.name for o in context.scene.objects if o.hide_get() or o.hide_viewport]
    if not hidden:
        return STATUS_PASS, "No hidden objects in the scene."
    text = ", ".join(hidden[:5])
    if len(hidden) > 5:
        text += " ..."
    return STATUS_FAIL, "Hidden objects: %s" % text


def check_backface_cull(objects, context):
    bad = []
    for obj in objects:
        for slot in obj.material_slots:
            mat = slot.material
            if mat is not None and not mat.use_backface_culling:
                bad.append("%s/%s" % (obj.name, mat.name))
    if not bad:
        return STATUS_PASS, "Backface culling is ON for all materials."
    return STATUS_WARN, "Backface culling OFF: %s" % ", ".join(bad[:5])


def check_transform(objects, context):
    bad = [o.name for o in objects if _has_transform(o)]
    if not bad:
        return STATUS_PASS, "No scale or rotation transform on the selection."
    return STATUS_FAIL, "Objects with transform: %s" % ", ".join(bad)


def check_polygons(objects, context):
    ngon_count = 0
    nonplanar_count = 0
    nonconvex_count = 0

    for obj in objects:
        bm, should_free = operators._bmesh_for_object(context, obj)
        for face in bm.faces:
            n = len(face.verts)
            if n > 4:
                ngon_count += 1
                continue
            if n == 4:
                verts = [v.co for v in face.verts]
                normal = face.normal
                if abs((verts[3] - verts[0]).dot(normal)) > 1e-4:
                    nonplanar_count += 1

                is_convex = True
                for i in range(4):
                    a, b, c = verts[i], verts[(i + 1) % 4], verts[(i + 2) % 4]
                    if (b - a).cross(c - b).dot(normal) < 0:
                        is_convex = False
                        break
                if not is_convex:
                    nonconvex_count += 1
        if should_free:
            bm.free()

    if ngon_count == 0 and nonplanar_count == 0 and nonconvex_count == 0:
        return STATUS_PASS, "All polygons are triangles or convex, planar quads."
    return (
        STATUS_WARN,
        "N-gons: %d, non-planar quads: %d, non-convex quads: %d"
        % (ngon_count, nonplanar_count, nonconvex_count),
    )


def check_materials(objects, context):
    problems = []
    orphan = [m.name for m in bpy.data.materials if m.users == 0]
    if orphan:
        problems.append("%d unused material(s) in the file" % len(orphan))

    for obj in objects:
        if not obj.material_slots or all(s.material is None for s in obj.material_slots):
            problems.append("%s has no material" % obj.name)

    if not problems:
        return STATUS_PASS, "No unused materials, all objects have a material assigned."
    return STATUS_WARN, "; ".join(problems)


def check_uv_bounds(objects, context):
    bad = []
    for obj in objects:
        bm, should_free = operators._bmesh_for_object(context, obj)
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is not None:
            for face in bm.faces:
                out_of_bounds = False
                for loop in face.loops:
                    uv = loop[uv_layer].uv
                    if uv.x < -1e-4 or uv.x > 1.0001 or uv.y < -1e-4 or uv.y > 1.0001:
                        out_of_bounds = True
                        break
                if out_of_bounds:
                    bad.append(obj.name)
                    break
        if should_free:
            bm.free()

    if not bad:
        return STATUS_PASS, "All UV shells are within the [0,1] area."
    return STATUS_WARN, "UV shells outside [0,1]: %s" % ", ".join(bad)


def check_uv_channels(objects, context):
    bad = [(o.name, len(o.data.uv_layers)) for o in objects if len(o.data.uv_layers) > 1]
    if not bad:
        return STATUS_INFO, "One UV map per object (or none)."
    return STATUS_INFO, "; ".join("%s: %d UV maps" % (n, c) for n, c in bad)


def check_uv_utilization(objects, context):
    results = []
    for obj in objects:
        bm, should_free = operators._bmesh_for_object(context, obj)
        uv_layer = bm.loops.layers.uv.active
        if uv_layer is not None:
            area = sum(operators._face_uv_area(f, uv_layer) for f in bm.faces)
            results.append((obj.name, area * 100.0))
        if should_free:
            bm.free()

    if not results:
        return STATUS_INFO, "No UV map to evaluate."

    worst = min(results, key=lambda r: r[1])
    avg = sum(r[1] for r in results) / len(results)

    if worst[1] < 50.0:
        status = STATUS_FAIL
    elif worst[1] < 75.0:
        status = STATUS_WARN
    else:
        status = STATUS_PASS

    return status, "Average UV utilization: %.0f%% (lowest: %s at %.0f%%)" % (
        avg,
        worst[0],
        worst[1],
    )


def check_material_id(objects, context):
    bad = [(o.name, len(o.material_slots)) for o in objects if len(o.material_slots) > 1]
    if not bad:
        return STATUS_PASS, "One material slot per object."
    return STATUS_INFO, "; ".join("%s: %d material slots" % (n, c) for n, c in bad)


CHECKS = (
    ("units", "1. Correct System Units", check_units, "uvtt.fix_units"),
    ("naming", "2. File / Object / Material Names", check_naming, None),
    ("pivot_origin", "3. Pivot at [0,0,0]", check_pivot_origin, "uvtt.fix_pivot_origin"),
    ("pivot_bbox", "4. Pivot Inside Bounding Box", check_pivot_bbox, "uvtt.fix_pivot_bbox"),
    ("hidden", "5. No Hidden Objects", check_hidden, "uvtt.fix_hidden"),
    ("backface", "6. Backface Culling ON", check_backface_cull, "uvtt.fix_backface_cull"),
    ("transform", "7. No Scale/Rotation Transform", check_transform, "uvtt.fix_transform"),
    ("polygons", "8. Correct Polygons", check_polygons, "uvtt.fix_polygons"),
    ("materials", "9. Correct Materials on Scene", check_materials, "uvtt.fix_materials"),
    ("uv_bounds", "10. UV Shells in [0,1]", check_uv_bounds, None),
    ("uv_channels", "11. Quantity of UV Maps", check_uv_channels, None),
    ("uv_utilization", "12. UV Utilization", check_uv_utilization, None),
    ("material_id", "14. Material Slots per Object", check_material_id, None),
)


def _status_icon(status):
    return {
        "PASS": "CHECKMARK",
        "FAIL": "CANCEL",
        "WARN": "ERROR",
        "INFO": "INFO",
    }.get(status, "QUESTION")


class UVTT_CheckResult(bpy.types.PropertyGroup):
    check_id: bpy.props.StringProperty()
    label: bpy.props.StringProperty()
    status: bpy.props.StringProperty()
    message: bpy.props.StringProperty()
    fix_id: bpy.props.StringProperty()


def _short_label(label):
    return label.split(". ", 1)[-1]


def _build_summary_tip(count, passed, results):
    if count == 0:
        return "Nothing checked yet."

    failed = [_short_label(r.label) for r in results if r.status == STATUS_FAIL]
    warned = [_short_label(r.label) for r in results if r.status == STATUS_WARN]

    parts = ["Checked %d object(s): %d/%d checks passed." % (count, passed, len(results))]
    if failed:
        parts.append("Needs attention: " + ", ".join(failed) + ".")
    if warned:
        parts.append("Worth a look: " + ", ".join(warned) + ".")
    if not failed and not warned:
        parts.append("Model looks ready for feedback!")
    return " ".join(parts)


def _run_all_checks(context):
    objects = _checker_objects(context)
    results = context.scene.uvtt_check_results
    results.clear()

    passed = 0
    for check_id, label, func, fix_id in CHECKS:
        status, message = func(objects, context)
        item = results.add()
        item.check_id = check_id
        item.label = label
        item.status = status
        item.message = message
        item.fix_id = fix_id or ""
        if status == STATUS_PASS:
            passed += 1

    context.scene["uvtt_checker_tip"] = _build_summary_tip(len(objects), passed, results)

    return len(objects), passed


class UVTT_OT_run_checker(bpy.types.Operator):
    """Run the Checker QA checklist on the selected object(s)"""

    bl_idname = "uvtt.run_checker"
    bl_label = "Run Check"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and bool(_checker_objects(context))

    def execute(self, context):
        count, passed = _run_all_checks(context)
        self.report(
            {"INFO"},
            "Checked %d object(s): %d/%d checks passed" % (count, passed, len(CHECKS)),
        )
        return {"FINISHED"}


class UVTT_OT_fix_units(bpy.types.Operator):
    """Set scene units to Metric with a scale of 1.0 (meters)"""

    bl_idname = "uvtt.fix_units"
    bl_label = "Fix Units"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        unit = context.scene.unit_settings
        unit.system = "METRIC"
        unit.scale_length = 1.0
        _run_all_checks(context)
        return {"FINISHED"}


class UVTT_OT_fix_pivot_origin(bpy.types.Operator):
    """Move the pivot of the selected object(s) to the world origin"""

    bl_idname = "uvtt.fix_pivot_origin"
    bl_label = "Fix Pivot Origin"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        objects = _checker_objects(context)
        if not objects:
            return {"CANCELLED"}

        cursor_loc = context.scene.cursor.location.copy()
        context.scene.cursor.location = (0.0, 0.0, 0.0)
        with context.temp_override(
            active_object=objects[0],
            selected_editable_objects=objects,
            selected_objects=objects,
            object=objects[0],
        ):
            bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
        context.scene.cursor.location = cursor_loc

        _run_all_checks(context)
        return {"FINISHED"}


class UVTT_OT_fix_pivot_bbox(bpy.types.Operator):
    """Move the pivot of the selected object(s) to the center of their bounding box"""

    bl_idname = "uvtt.fix_pivot_bbox"
    bl_label = "Fix Pivot BBox"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        objects = _checker_objects(context)
        if not objects:
            return {"CANCELLED"}

        with context.temp_override(
            active_object=objects[0],
            selected_editable_objects=objects,
            selected_objects=objects,
            object=objects[0],
        ):
            bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")

        _run_all_checks(context)
        return {"FINISHED"}


class UVTT_OT_fix_hidden(bpy.types.Operator):
    """Unhide every object and collection in the scene"""

    bl_idname = "uvtt.fix_hidden"
    bl_label = "Fix Hidden"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        for obj in context.scene.objects:
            obj.hide_set(False)
            obj.hide_viewport = False
        for coll in bpy.data.collections:
            coll.hide_viewport = False

        _run_all_checks(context)
        return {"FINISHED"}


class UVTT_OT_fix_backface_cull(bpy.types.Operator):
    """Enable backface culling on all materials of the selected object(s)"""

    bl_idname = "uvtt.fix_backface_cull"
    bl_label = "Fix Backface Cull"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        for obj in _checker_objects(context):
            for slot in obj.material_slots:
                if slot.material is not None:
                    slot.material.use_backface_culling = True

        _run_all_checks(context)
        return {"FINISHED"}


class UVTT_OT_fix_transform(bpy.types.Operator):
    """Apply rotation and scale on the selected object(s)"""

    bl_idname = "uvtt.fix_transform"
    bl_label = "Fix Transform"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        objects = _checker_objects(context)
        if not objects:
            return {"CANCELLED"}

        with context.temp_override(
            active_object=objects[0],
            selected_editable_objects=objects,
            selected_objects=objects,
            object=objects[0],
        ):
            bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

        _run_all_checks(context)
        return {"FINISHED"}


class UVTT_OT_fix_polygons(SafetyConfirmMixin, bpy.types.Operator):
    """Triangulate n-gons on the selected object(s). This changes topology"""

    bl_idname = "uvtt.fix_polygons"
    bl_label = "Fix Polygons"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        self._save_if_dirty(context)

        for obj in _checker_objects(context):
            bm, should_free = operators._bmesh_for_object(context, obj)
            ngons = [f for f in bm.faces if len(f.verts) > 4]
            if ngons:
                bmesh.ops.triangulate(bm, faces=ngons, quad_method="BEAUTY", ngon_method="BEAUTY")

            if should_free:
                bm.to_mesh(obj.data)
                obj.data.update()
                bm.free()
            else:
                bmesh.update_edit_mesh(obj.data)

        _run_all_checks(context)
        return {"FINISHED"}


class UVTT_OT_fix_materials(bpy.types.Operator):
    """Assign a simple default material to objects that don't have one"""

    bl_idname = "uvtt.fix_materials"
    bl_label = "Fix Materials"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        for obj in _checker_objects(context):
            if not obj.material_slots or all(s.material is None for s in obj.material_slots):
                mat = bpy.data.materials.new("Material")
                mat.use_nodes = True
                if obj.data.materials:
                    obj.data.materials[0] = mat
                else:
                    obj.data.materials.append(mat)

        _run_all_checks(context)
        return {"FINISHED"}


class UVTT_OT_prepare_mesh(SafetyConfirmMixin, bpy.types.Operator):
    """Batch-prepare the selected mesh(es) for feedback: unhide, unfreeze, reset transform, backface cull ON, assign a material if missing"""

    bl_idname = "uvtt.prepare_mesh"
    bl_label = "Prepare Mesh"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and bool(_checker_objects(context))

    def execute(self, context):
        self._save_if_dirty(context)

        objects = _checker_objects(context)

        with context.temp_override(
            active_object=objects[0],
            selected_editable_objects=objects,
            selected_objects=objects,
            object=objects[0],
        ):
            bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

        for obj in objects:
            obj.hide_set(False)
            obj.hide_viewport = False
            obj.hide_select = False

            bm, should_free = operators._bmesh_for_object(context, obj)
            for elem_seq in (bm.verts, bm.edges, bm.faces):
                for elem in elem_seq:
                    elem.hide = False
            if should_free:
                bm.to_mesh(obj.data)
                obj.data.update()
                bm.free()
            else:
                bmesh.update_edit_mesh(obj.data)

            if not obj.material_slots or all(s.material is None for s in obj.material_slots):
                mat = bpy.data.materials.new(obj.name + "_mat")
                mat.use_nodes = True
                if obj.data.materials:
                    obj.data.materials[0] = mat
                else:
                    obj.data.materials.append(mat)

            for slot in obj.material_slots:
                if slot.material is not None:
                    slot.material.use_backface_culling = True

        self.report({"INFO"}, "Prepared %d object(s) for feedback." % len(objects))
        return {"FINISHED"}


class UVTT_OT_prepare_scene(SafetyConfirmMixin, bpy.types.Operator):
    """Batch-prepare the scene for feedback: Metric units, unhide/unfreeze everything, relative texture paths"""

    bl_idname = "uvtt.prepare_scene"
    bl_label = "Prepare Scene"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        self._save_if_dirty(context)

        unit = context.scene.unit_settings
        unit.system = "METRIC"
        unit.scale_length = 1.0

        for obj in context.scene.objects:
            obj.hide_set(False)
            obj.hide_viewport = False
            obj.hide_select = False
        for coll in bpy.data.collections:
            coll.hide_viewport = False

        relativized = False
        if bpy.data.filepath:
            bpy.ops.file.make_paths_relative()
            relativized = True

        message = "Scene prepared: Metric units, everything unhidden and unfrozen."
        if relativized:
            message += " Texture paths made relative."
        else:
            message += " Save the file to also relativize texture paths."
        self.report({"INFO"}, message)
        return {"FINISHED"}


def _build_log_text(context, objects):
    lines = [
        "TechArt Tools -- Checker Log",
        "Run at: %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Objects checked: %s" % (", ".join(o.name for o in objects) if objects else "none"),
        "",
    ]
    for item in context.scene.uvtt_check_results:
        lines.append("%s -- %s" % (item.label, item.status))
        lines.append("    %s" % item.message)
    return "\n".join(lines)


class UVTT_OT_open_checker_log(bpy.types.Operator):
    """Write the last check results to a text block and print them to the console"""

    bl_idname = "uvtt.open_checker_log"
    bl_label = "Open Log"
    bl_options = {"REGISTER"}

    def execute(self, context):
        if not context.scene.uvtt_check_results:
            self.report({"WARNING"}, "Nothing to log yet - run Check first")
            return {"CANCELLED"}

        objects = _checker_objects(context)
        log_text = _build_log_text(context, objects)

        text = bpy.data.texts.get(LOG_TEXT_NAME)
        if text is None:
            text = bpy.data.texts.new(LOG_TEXT_NAME)
        text.clear()
        text.write(log_text)

        print(log_text)

        self.report(
            {"INFO"},
            "Log written to text '%s' and printed to the console (Window > Toggle System "
            "System Console). Open a Text Editor and select it to read." % LOG_TEXT_NAME,
        )
        return {"FINISHED"}


def _count_triangles(bm):
    return sum(max(len(f.verts) - 2, 0) for f in bm.faces)


def _count_uv_loops(bm):
    return sum(len(f.loops) for f in bm.faces)


def _count_armature_bones(obj):
    for mod in obj.modifiers:
        if mod.type == "ARMATURE" and mod.object is not None and mod.object.data is not None:
            return len(mod.object.data.bones)
    return 0


def _uv_island_count(bm, uv_layer):
    def edge_uv_key(face, edge):
        for loop in face.loops:
            if loop.edge == edge:
                a = loop[uv_layer].uv
                b = loop.link_loop_next[uv_layer].uv
                return frozenset({(round(a.x, 5), round(a.y, 5)), (round(b.x, 5), round(b.y, 5))})
        return None

    visited = set()
    islands = 0

    for start_face in bm.faces:
        if start_face.index in visited:
            continue
        islands += 1
        stack = [start_face]
        visited.add(start_face.index)
        while stack:
            face = stack.pop()
            for edge in face.edges:
                linked = edge.link_faces
                if len(linked) != 2:
                    continue
                other = linked[0] if linked[1] == face else linked[1]
                if other.index in visited:
                    continue
                if edge_uv_key(face, edge) == edge_uv_key(other, edge):
                    visited.add(other.index)
                    stack.append(other)

    return islands


class UVTT_OT_get_statistics(bpy.types.Operator):
    """Report extended mesh and UV statistics for the selected object(s)"""

    bl_idname = "uvtt.get_statistics"
    bl_label = "Get Statistics"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return bool(_checker_objects(context))

    def execute(self, context):
        objects = _checker_objects(context)

        total_polys = 0
        total_tris = 0
        total_verts = 0
        total_bones = 0
        total_uv_verts = 0
        material_counts = []
        uv_bounds_ok = True
        uv_set_counts = []
        uv_shell_total = 0
        uv_areas = []
        overlap_present = False

        for obj in objects:
            bm, should_free = operators._bmesh_for_object(context, obj)

            total_polys += len(bm.faces)
            total_tris += _count_triangles(bm)
            total_verts += len(bm.verts)
            total_bones += _count_armature_bones(obj)
            material_counts.append(len(obj.material_slots))

            uv_layer = bm.loops.layers.uv.active
            if uv_layer is not None:
                total_uv_verts += _count_uv_loops(bm)
                uv_set_counts.append(len(obj.data.uv_layers))
                uv_shell_total += _uv_island_count(bm, uv_layer)

                area = sum(operators._face_uv_area(f, uv_layer) for f in bm.faces) * 100.0
                uv_areas.append(area)
                if area > 100.5:
                    overlap_present = True

                for face in bm.faces:
                    for loop in face.loops:
                        uv = loop[uv_layer].uv
                        if uv.x < -1e-4 or uv.x > 1.0001 or uv.y < -1e-4 or uv.y > 1.0001:
                            uv_bounds_ok = False

            if should_free:
                bm.free()

        lines = []
        lines.append("Mesh objects: %d" % len(objects))
        lines.append("Polygons: %d" % total_polys)
        lines.append("Triangles: %d" % total_tris)
        lines.append("Vertices: %d" % total_verts)
        if total_bones:
            lines.append("Bones: %d" % total_bones)
        lines.append("UV-vertices: %d" % total_uv_verts)
        if material_counts:
            if len(set(material_counts)) == 1:
                lines.append("Materials: %d" % material_counts[0])
            else:
                lines.append(
                    "Materials: %d-%d across objects" % (min(material_counts), max(material_counts))
                )
        lines.append("UV in [0,1]: %s" % ("True" if uv_bounds_ok else "False"))
        if uv_set_counts:
            if len(set(uv_set_counts)) == 1:
                lines.append("UV Maps: %d" % uv_set_counts[0])
            else:
                lines.append("UV Maps: %d-%d across objects" % (min(uv_set_counts), max(uv_set_counts)))
        lines.append("UV Shells: %d" % uv_shell_total)
        if uv_areas:
            avg = sum(uv_areas) / len(uv_areas)
            lines.append("UV Utilization (average): %.0f%%" % avg)
            if len(uv_areas) > 1:
                lines.append("UV Utilization (lowest): %.0f%%" % min(uv_areas))
        if overlap_present:
            lines.append("Overlap: likely present (UV area sum is over 100%)")
        else:
            lines.append("Overlap: none detected (area-sum check only, not pixel-exact)")

        context.scene["uvtt_statistics_text"] = "\n".join(lines)

        self.report({"INFO"}, "Statistics computed for %d object(s)" % len(objects))
        return {"FINISHED"}


BOUND_BOX_EDGES = (
    (0, 1), (1, 2), (2, 3), (3, 0),
    (4, 5), (5, 6), (6, 7), (7, 4),
    (0, 4), (1, 5), (2, 6), (3, 7),
)

_dimension_state = {"view_handle": None, "pixel_handle": None}


def _dimension_targets():
    return [o for o in bpy.context.selected_objects if o.type == "MESH"]


def _draw_dimensions_3d():
    objects = _dimension_targets()
    if not objects:
        return

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")
    gpu.state.line_width_set(1.5)
    shader.bind()
    shader.uniform_float("color", (0.6, 0.85, 1.0, 0.9))

    for obj in objects:
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        coords = []
        for a, b in BOUND_BOX_EDGES:
            coords.append(corners[a])
            coords.append(corners[b])
        batch = batch_for_shader(shader, "LINES", {"pos": coords})
        batch.draw(shader)

    gpu.state.blend_set("NONE")
    gpu.state.line_width_set(1.0)


def _draw_dimensions_text():
    context = bpy.context
    region = context.region
    rv3d = context.region_data
    if region is None or rv3d is None:
        return

    objects = _dimension_targets()
    if not objects:
        return

    for obj in objects:
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        xs = [c.x for c in corners]
        ys = [c.y for c in corners]
        zs = [c.z for c in corners]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        min_z, max_z = min(zs), max(zs)
        mid_x = (min_x + max_x) / 2.0
        mid_y = (min_y + max_y) / 2.0
        mid_z = (min_z + max_z) / 2.0

        labels = (
            ("Width: %.3fm" % (max_x - min_x), Vector((mid_x, min_y, min_z)), abs(obj.scale.x - 1.0) > 1e-4),
            ("Length: %.3fm" % (max_y - min_y), Vector((min_x, mid_y, min_z)), abs(obj.scale.y - 1.0) > 1e-4),
            ("Height: %.3fm" % (max_z - min_z), Vector((min_x, min_y, mid_z)), abs(obj.scale.z - 1.0) > 1e-4),
        )

        for text, world_point, has_transform in labels:
            coord = view3d_utils.location_3d_to_region_2d(region, rv3d, world_point)
            if coord is None:
                continue
            if has_transform:
                blf.color(0, 1.0, 0.35, 0.35, 1.0)
            else:
                blf.color(0, 1.0, 1.0, 1.0, 1.0)
            blf.position(0, coord.x, coord.y, 0)
            blf.size(0, 14)
            blf.draw(0, text)


class UVTT_OT_toggle_dimensions(bpy.types.Operator):
    """Toggle an in-viewport bounding-box size overlay for the selected object(s).

    Text turns red on an axis where the object has a non-1.0 scale, since the
    displayed size only reflects the mesh data, not that scale transform.
    """

    bl_idname = "uvtt.toggle_dimensions"
    bl_label = "Show Dimension"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return bool(_checker_objects(context))

    def execute(self, context):
        if _dimension_state["view_handle"] is not None:
            bpy.types.SpaceView3D.draw_handler_remove(_dimension_state["view_handle"], "WINDOW")
            bpy.types.SpaceView3D.draw_handler_remove(_dimension_state["pixel_handle"], "WINDOW")
            _dimension_state["view_handle"] = None
            _dimension_state["pixel_handle"] = None
            self.report({"INFO"}, "Show Dimension is OFF")
        else:
            _dimension_state["view_handle"] = bpy.types.SpaceView3D.draw_handler_add(
                _draw_dimensions_3d, (), "WINDOW", "POST_VIEW"
            )
            _dimension_state["pixel_handle"] = bpy.types.SpaceView3D.draw_handler_add(
                _draw_dimensions_text, (), "WINDOW", "POST_PIXEL"
            )
            self.report({"INFO"}, "Show Dimension is ON")

        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()

        return {"FINISHED"}


def _remove_dimension_handlers():
    if _dimension_state["view_handle"] is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_dimension_state["view_handle"], "WINDOW")
        _dimension_state["view_handle"] = None
    if _dimension_state["pixel_handle"] is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_dimension_state["pixel_handle"], "WINDOW")
        _dimension_state["pixel_handle"] = None


class UVTT_PT_viewport_guide(bpy.types.Panel):
    bl_label = "TechArt Tools"
    bl_idname = "UVTT_PT_viewport_guide"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "TechArt Tools"

    def draw(self, context):
        self.bl_label = "TechArt Tools v%s" % operators.TECHART_VERSION
        layout = self.layout
        layout.operator("wm.url_open", text="TechArt Tools Online Guide", icon="URL").url = (
            operators.TECHART_URL
        )


class UVTT_PT_prepare(bpy.types.Panel):
    bl_label = "Preparation"
    bl_idname = "UVTT_PT_prepare"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "TechArt Tools"

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="Mesh")
        box.operator("uvtt.prepare_mesh", text="Prepare Mesh", icon="MESH_DATA")

        box = layout.box()
        box.label(text="Scene")
        box.operator("uvtt.prepare_scene", text="Prepare Scene", icon="SCENE_DATA")


class UVTT_PT_statistics(bpy.types.Panel):
    bl_label = "Statistics"
    bl_idname = "UVTT_PT_statistics"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "TechArt Tools"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        layout.operator("uvtt.get_statistics", text="Get Statistics", icon="INFO")

        text = context.scene.get("uvtt_statistics_text")
        if text:
            box = layout.box()
            col = box.column(align=True)
            for line in text.split("\n"):
                col.label(text=line)

            image_name = context.scene.get("uvtt_uv_utilization_image")
            image = bpy.data.images.get(image_name) if image_name else None
            if image is not None:
                box.label(text="UV coverage preview (last UV Utilization check):")
                box.template_icon(icon_value=image.preview.icon_id, scale=4.0)

        box = layout.box()
        box.label(text="Dimension Overlay")
        is_on = _dimension_state["view_handle"] is not None
        box.operator(
            "uvtt.toggle_dimensions",
            text="Hide Dimension" if is_on else "Show Dimension",
            icon="EMPTY_AXIS",
        )


class UVTT_PT_material(bpy.types.Panel):
    bl_label = "Material"
    bl_idname = "UVTT_PT_material"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "TechArt Tools"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="Common")
        row = box.row(align=True)
        row.operator("uvtt.set_gloss", text="Gloss", icon="SHADING_RENDERED")
        row.operator("uvtt.set_matte", text="Matte", icon="SHADING_SOLID")
        row.operator("uvtt.set_normal_check", text="NM", icon="NORMALS_FACE")
        box.operator("uvtt.reset_material", text="Reset", icon="LOOP_BACK")

        box = layout.box()
        box.label(text="Bake AO")
        box.prop(context.scene, "uvtt_bake_ao_size", text="Map Size (px)")
        box.prop(context.scene, "uvtt_bake_ao_samples", text="Samples")
        box.prop(context.scene, "uvtt_bake_ao_margin", text="Margin (px)")
        box.prop(context.scene, "uvtt_bake_ao_denoise")
        box.operator("uvtt.bake_ao", text="Bake AO", icon="IMAGE_DATA")
        if not bpy.data.filepath:
            box.label(text="Save the .blend file first", icon="ERROR")

        box = layout.box()
        box.label(text="Base Texture Set")
        box.prop(context.scene, "uvtt_basetex_size", text="Map Size (px)")
        box.prop(context.scene, "uvtt_basetex_albedo_color", text="Albedo")
        box.prop(context.scene, "uvtt_basetex_metal", text="Metal")
        box.prop(context.scene, "uvtt_basetex_ao", text="AO")
        box.prop(context.scene, "uvtt_basetex_roughness", text="Roughness")
        box.label(text='Normal: flat, saved as "_nm"')
        box.operator("uvtt.generate_base_tex", text="Generate Base Tex", icon="TEXTURE")
        if not bpy.data.filepath:
            box.label(text="Save the .blend file first", icon="ERROR")


class UVTT_PT_checker(bpy.types.Panel):
    bl_label = "Checker"
    bl_idname = "UVTT_PT_checker"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "TechArt Tools"

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.operator("uvtt.run_checker", text="Run Check", icon="CHECKMARK")
        row.operator("uvtt.open_checker_log", text="Open Log", icon="TEXT")

        results = context.scene.uvtt_check_results
        if not results:
            layout.label(text="Not checked yet.")
            return

        passed = sum(1 for r in results if r.status == STATUS_PASS)
        layout.label(text="%d / %d checks passed" % (passed, len(results)))

        for item in results:
            box = layout.box()
            box.label(text=item.label, icon=_status_icon(item.status))
            col = box.column(align=True)
            for line in operators.wrap_text_for_region(context, item.message):
                col.label(text=line)
            if item.fix_id:
                box.operator(item.fix_id, text="Fix", icon="TOOL_SETTINGS")


def _safe_filename(name):
    invalid = '<>:"/\\|?*'
    cleaned = "".join("_" if c in invalid else c for c in name).strip()
    return cleaned or "export"


def _export_name(context):
    if not context.scene.uvtt_export_use_mesh_name:
        return None
    objects = _checker_objects(context)
    if not objects:
        return None
    active = context.active_object
    obj = active if active in objects else objects[0]
    return _safe_filename(obj.name)


def _export_filepath(extension, name_override=None):
    if not bpy.data.filepath:
        return None
    directory = os.path.dirname(bpy.data.filepath)
    base_name = name_override or os.path.splitext(os.path.basename(bpy.data.filepath))[0]
    return os.path.join(directory, base_name + extension)


class SaveFileMixin:
    """Shared invoke()/save-check for operators whose output path is derived
    from the saved .blend file location.

    If the file was never saved there is no path to derive an output from, so
    we just warn and cancel - Blender can't reliably chain a Save As file
    browser into the rest of this operator. If the file has a path but
    unsaved changes, we offer a Save & Continue / Cancel popup instead of
    silently exporting stale data or silently saving without asking.
    """

    def invoke(self, context, event):
        if not bpy.data.filepath:
            self.report(
                {"WARNING"}, "Save the .blend file first - the output path is derived from it"
            )
            return {"CANCELLED"}
        if bpy.data.is_dirty:
            return context.window_manager.invoke_confirm(
                self,
                event,
                title="Unsaved Changes",
                message="Save the file before continuing?",
                confirm_text="Save & Continue",
            )
        return self.execute(context)

    def _ensure_saved(self, context):
        if not bpy.data.filepath:
            self.report(
                {"WARNING"}, "Save the .blend file first - the output path is derived from it"
            )
            return False
        if bpy.data.is_dirty:
            bpy.ops.wm.save_mainfile()
        return True


class UVTT_OT_export_fbx(SaveFileMixin, bpy.types.Operator):
    """Export the selected mesh object(s) to FBX next to the saved .blend file.

    A hard, minimal export: geometry, UV and normals (with tangent space) only -
    no animation, cameras, lights or embedded textures.
    """

    bl_idname = "uvtt.export_fbx"
    bl_label = "Export FBX"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return bool(_checker_objects(context))

    def execute(self, context):
        if not self._ensure_saved(context):
            return {"CANCELLED"}

        filepath = _export_filepath(".fbx", _export_name(context))

        bpy.ops.export_scene.fbx(
            filepath=filepath,
            check_existing=False,
            use_selection=True,
            object_types={"MESH"},
            use_custom_props=False,
            global_scale=1.0,
            apply_unit_scale=False,
            apply_scale_options="FBX_SCALE_UNITS",
            axis_forward="-Z",
            axis_up="Y",
            use_space_transform=True,
            bake_space_transform=False,
            mesh_smooth_type="OFF",
            use_tspace=True,
            use_triangles=True,
            use_mesh_modifiers=True,
            use_mesh_edges=False,
            use_subsurf=False,
            bake_anim=False,
            embed_textures=False,
            path_mode="AUTO",
            batch_mode="OFF",
        )

        self.report({"INFO"}, "Exported to %s" % filepath)
        return {"FINISHED"}


class UVTT_OT_export_obj(SaveFileMixin, bpy.types.Operator):
    """Export the selected mesh object(s) to OBJ next to the saved .blend file.

    A hard, minimal export: geometry, UV and normals only.
    """

    bl_idname = "uvtt.export_obj"
    bl_label = "Export OBJ"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return bool(_checker_objects(context))

    def execute(self, context):
        if not self._ensure_saved(context):
            return {"CANCELLED"}

        filepath = _export_filepath(".obj", _export_name(context))

        bpy.ops.wm.obj_export(
            filepath=filepath,
            check_existing=False,
            export_selected_objects=True,
            export_uv=True,
            export_normals=True,
            export_materials=True,
            export_triangulated_mesh=True,
            apply_modifiers=True,
            forward_axis="NEGATIVE_Z",
            up_axis="Y",
            global_scale=1.0,
        )

        self.report({"INFO"}, "Exported to %s" % filepath)
        return {"FINISHED"}


INTERSECTION_PREFIX = "UVTT_IntersectionCheck_"


def _intersection_material():
    mat_name = "UVTT_IntersectionCheck"
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        mat = bpy.data.materials.new(mat_name)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = (1.0, 0.0, 0.0, 1.0)
            if "Emission Color" in bsdf.inputs:
                bsdf.inputs["Emission Color"].default_value = (1.0, 0.0, 0.0, 1.0)
                bsdf.inputs["Emission Strength"].default_value = 1.0
        mat["uvtt_generated"] = True
    return mat


class UVTT_OT_check_intersection(bpy.types.Operator):
    """Highlight open (boundary) edges with a red tube.

    Where two parts are meant to overlap (a bolt into a block, for example) an
    open edge that stays close to the surface means the intersection there is
    too shallow. Visible red tubes point at those spots.
    """

    bl_idname = "uvtt.check_intersection"
    bl_label = "Check"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and bool(_checker_objects(context))

    def execute(self, context):
        objects = _checker_objects(context)
        depth_m = context.scene.uvtt_intersection_depth / 1000.0

        mat = _intersection_material()
        created = 0

        for obj in objects:
            bm, should_free = operators._bmesh_for_object(context, obj)
            open_edges = [e for e in bm.edges if len(e.link_faces) == 1]

            if open_edges:
                verts_map = {}
                mesh_verts = []
                mesh_edges = []
                for edge in open_edges:
                    idxs = []
                    for v in edge.verts:
                        if v.index not in verts_map:
                            verts_map[v.index] = len(mesh_verts)
                            mesh_verts.append(v.co.copy())
                        idxs.append(verts_map[v.index])
                    mesh_edges.append(tuple(idxs))

                helper_name = INTERSECTION_PREFIX + obj.name
                mesh = bpy.data.meshes.new(helper_name)
                mesh.from_pydata(mesh_verts, mesh_edges, [])
                mesh.update()

                helper = bpy.data.objects.new(helper_name, mesh)
                helper.matrix_world = obj.matrix_world.copy()
                helper["uvtt_intersection_check"] = True
                context.collection.objects.link(helper)

                with context.temp_override(
                    active_object=helper,
                    selected_editable_objects=[helper],
                    selected_objects=[helper],
                    object=helper,
                ):
                    bpy.ops.object.convert(target="CURVE")

                curve_data = helper.data
                curve_data.bevel_depth = depth_m
                curve_data.bevel_resolution = 2
                curve_data.fill_mode = "FULL"
                if curve_data.materials:
                    curve_data.materials[0] = mat
                else:
                    curve_data.materials.append(mat)

                created += 1

            if should_free:
                bm.free()

        if created == 0:
            self.report({"INFO"}, "No open edges found - no intersection problems detected")
        else:
            operators._set_material_preview_shading(context)
            self.report(
                {"INFO"},
                "Created intersection-check geometry on %d object(s). A visible red tube "
                "means the intersection there is too shallow." % created,
            )

        return {"FINISHED"}


class UVTT_OT_clean_intersection(bpy.types.Operator):
    """Remove the intersection-check helper geometry"""

    bl_idname = "uvtt.clean_intersection"
    bl_label = "Clean"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        removed = 0
        for obj in list(bpy.data.objects):
            if not obj.get("uvtt_intersection_check"):
                continue
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data is not None and data.users == 0:
                if isinstance(data, bpy.types.Curve):
                    bpy.data.curves.remove(data)
                elif isinstance(data, bpy.types.Mesh):
                    bpy.data.meshes.remove(data)
            removed += 1

        if removed == 0:
            self.report({"INFO"}, "Nothing to clean")
        else:
            self.report({"INFO"}, "Removed %d intersection-check object(s)" % removed)

        return {"FINISHED"}


def _preview_filepath():
    if not bpy.data.filepath:
        return None
    base = os.path.splitext(bpy.data.filepath)[0]
    return base + "_preview.jpg"


class UVTT_OT_render_preview(SaveFileMixin, bpy.types.Operator):
    """Snapshot the current Perspective viewport and save it next to the .blend file.

    Frames the selected object(s) if something is selected, the whole scene
    otherwise.
    """

    bl_idname = "uvtt.render_preview"
    bl_label = "Render Preview"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def execute(self, context):
        if not self._ensure_saved(context):
            return {"CANCELLED"}

        filepath = _preview_filepath()

        region_3d = context.region_data
        if region_3d is None:
            self.report({"WARNING"}, "Run this from the 3D Viewport")
            return {"CANCELLED"}

        if region_3d.view_perspective != "PERSP":
            region_3d.view_perspective = "PERSP"

        if context.selected_objects:
            bpy.ops.view3d.view_selected()
        else:
            bpy.ops.view3d.view_all()

        scene = context.scene
        prev_filepath = scene.render.filepath
        prev_format = scene.render.image_settings.file_format
        prev_use_ext = scene.render.use_file_extension

        scene.render.filepath = filepath
        scene.render.image_settings.file_format = "JPEG"
        scene.render.use_file_extension = False

        try:
            bpy.ops.render.opengl(write_still=True)
        finally:
            scene.render.filepath = prev_filepath
            scene.render.image_settings.file_format = prev_format
            scene.render.use_file_extension = prev_use_ext

        self.report({"INFO"}, "Preview saved to %s" % filepath)
        return {"FINISHED"}


LOD_LEVELS = 3
LOD_MIN_FACES = 12


class UVTT_OT_auto_lod(SafetyConfirmMixin, bpy.types.Operator):
    """Build an LOD chain for the selected mesh(es).

    Creates a "<name>_LODS" collection, moves the original into it as LOD0
    (renamed, unchanged otherwise), then adds progressively decimated copies
    LOD1-LOD3, each roughly "Reduction" smaller than the one before. Stops
    early - producing fewer levels - if another step would drop below a
    safe triangle count, instead of collapsing a LOD down to almost nothing.
    Every generated LOD is a plain mesh with no live modifiers.
    """

    bl_idname = "uvtt.auto_lod"
    bl_label = "Generate LODs"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and bool(_checker_objects(context))

    def execute(self, context):
        self._save_if_dirty(context)

        ratio = context.scene.uvtt_lod_ratio
        objects = _checker_objects(context)

        total_created = 0
        stopped_early = 0
        per_object_summaries = []

        for obj in objects:
            base_name = obj.name
            base_data_name = obj.data.name

            lods_collection = bpy.data.collections.new(base_name + "_LODS")
            context.collection.children.link(lods_collection)

            for coll in list(obj.users_collection):
                coll.objects.unlink(obj)
            lods_collection.objects.link(obj)
            obj.name = base_name + "_LOD0"
            obj.data.name = base_data_name + "_LOD0"

            previous = obj
            previous_faces = len(previous.data.polygons)
            lod_summary = ["%s (%d polys)" % (obj.name, previous_faces)]

            for level in range(1, LOD_LEVELS + 1):
                target_faces = max(1, round(previous_faces * ratio))
                if target_faces < LOD_MIN_FACES:
                    stopped_early += 1
                    break

                new_obj = previous.copy()
                new_obj.data = previous.data.copy()
                new_obj.name = "%s_LOD%d" % (base_name, level)
                new_obj.data.name = "%s_LOD%d" % (base_data_name, level)
                lods_collection.objects.link(new_obj)

                mod = new_obj.modifiers.new("Decimate", "DECIMATE")
                mod.ratio = ratio
                mod.decimate_type = "COLLAPSE"

                with context.temp_override(
                    active_object=new_obj,
                    selected_editable_objects=[new_obj],
                    selected_objects=[new_obj],
                    object=new_obj,
                ):
                    bpy.ops.object.convert(target="MESH")

                previous = new_obj
                previous_faces = len(new_obj.data.polygons)
                lod_summary.append("%s (%d polys)" % (new_obj.name, previous_faces))
                total_created += 1

            if len(lod_summary) > 1:
                per_object_summaries.append(
                    "%s: %s" % (base_name, ", ".join(lod_summary))
                )

        if total_created == 0:
            self.report({"WARNING"}, "No LODs created - nothing to reduce")
            return {"CANCELLED"}

        message = "Created %d LOD level(s) across %d object(s)" % (total_created, len(objects))
        if stopped_early:
            message += " - stopped early on %d object(s) to avoid over-simplifying" % stopped_early
        self.report({"INFO"}, message)

        tip = "Generated LODs. " + " | ".join(per_object_summaries)
        if stopped_early:
            tip += " Stopped early on %d object(s) to avoid over-simplifying." % stopped_early
        context.scene["uvtt_checker_tip"] = tip
        return {"FINISHED"}


def _bake_ao_material(image):
    """Get-or-create a plain white material with an Image Texture node
    plugged into Base Color, wired the same way Blender's own "simple bake
    target" setup looks - and mark that node active/selected, since that is
    how Cycles decides which image a bake writes into.
    """

    mat_name = "UVTT_BakeAO_" + image.name
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        mat = bpy.data.materials.new(mat_name)
        mat.use_nodes = True

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    bsdf = nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.5
        if "Metallic" in bsdf.inputs:
            bsdf.inputs["Metallic"].default_value = 0.0

    tex_node = nodes.get("UVTT_BakeImage")
    if tex_node is None:
        tex_node = nodes.new("ShaderNodeTexImage")
        tex_node.name = "UVTT_BakeImage"
    tex_node.image = image

    if bsdf is not None:
        operators._ensure_link(links, tex_node.outputs["Color"], bsdf.inputs["Base Color"])

    for node in nodes:
        node.select = False
    tex_node.select = True
    nodes.active = tex_node

    mat["uvtt_generated"] = True
    return mat


class UVTT_OT_bake_ao(SaveFileMixin, bpy.types.Operator):
    """Bake self-only Ambient Occlusion for the selected mesh(es) to a PNG.

    Builds a simple white material with an Image Texture node for each
    object and bakes into it with Cycles. Every other object in the scene is
    temporarily hidden from render while an object bakes, so the result is
    the object shading itself - not other objects casting shadows onto it.
    The image is saved as "<mesh name>_ao.png" next to the .blend file.
    """

    bl_idname = "uvtt.bake_ao"
    bl_label = "Bake AO"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and bool(_checker_objects(context))

    def execute(self, context):
        if not self._ensure_saved(context):
            return {"CANCELLED"}

        all_objects = _checker_objects(context)
        objects = [obj for obj in all_objects if obj.data.uv_layers]
        skipped_no_uv = len(all_objects) - len(objects)
        if not objects:
            self.report({"WARNING"}, "Selected object(s) have no UV map to bake into")
            return {"CANCELLED"}

        size = int(context.scene.uvtt_bake_ao_size)
        samples = context.scene.uvtt_bake_ao_samples
        margin = context.scene.uvtt_bake_ao_margin
        denoise = context.scene.uvtt_bake_ao_denoise
        directory = os.path.dirname(bpy.data.filepath)

        scene = context.scene
        prev_engine = scene.render.engine
        prev_samples = scene.cycles.samples
        prev_denoise = scene.cycles.use_denoising
        prev_selected = list(context.selected_objects)
        prev_active = context.view_layer.objects.active

        scene.render.engine = "CYCLES"
        scene.cycles.samples = samples
        scene.cycles.use_denoising = denoise

        baked = []

        try:
            for obj in objects:
                image_name = _safe_filename(obj.name) + "_ao"
                image = bpy.data.images.get(image_name)
                if image is not None and tuple(image.size) != (size, size):
                    bpy.data.images.remove(image)
                    image = None
                if image is None:
                    image = bpy.data.images.new(image_name, width=size, height=size)
                image.colorspace_settings.name = "Non-Color"

                mat = _bake_ao_material(image)
                operators._remember_original_material(obj)
                if obj.data.materials:
                    obj.data.materials[0] = mat
                else:
                    obj.data.materials.append(mat)

                hidden = []
                for other in scene.objects:
                    if other is not obj and not other.hide_render:
                        other.hide_render = True
                        hidden.append(other)

                for o in context.view_layer.objects:
                    o.select_set(o is obj)
                context.view_layer.objects.active = obj

                try:
                    bpy.ops.object.bake(
                        type="AO",
                        margin=margin,
                        margin_type="ADJACENT_FACES",
                        use_clear=True,
                    )
                finally:
                    for other in hidden:
                        other.hide_render = False

                filepath = os.path.join(directory, image_name + ".png")
                image.filepath_raw = filepath
                image.file_format = "PNG"
                image.save()

                baked.append("%s -> %s (%dx%d)" % (obj.name, os.path.basename(filepath), size, size))
        finally:
            scene.render.engine = prev_engine
            scene.cycles.samples = prev_samples
            scene.cycles.use_denoising = prev_denoise
            for o in context.view_layer.objects:
                o.select_set(o in prev_selected)
            context.view_layer.objects.active = prev_active

        if not baked:
            self.report({"WARNING"}, "Nothing baked")
            return {"CANCELLED"}

        message = "Baked AO for %d object(s)" % len(baked)
        if skipped_no_uv:
            message += " - skipped %d object(s) with no UV map" % skipped_no_uv
        self.report({"INFO"}, message)
        context.scene["uvtt_checker_tip"] = "Baked AO. " + " | ".join(baked)
        return {"FINISHED"}


def _get_or_new_image(name, size, alpha=False):
    image = bpy.data.images.get(name)
    if image is not None and tuple(image.size) != (size, size):
        bpy.data.images.remove(image)
        image = None
    if image is None:
        image = bpy.data.images.new(name, width=size, height=size, alpha=alpha)
    if alpha:
        image.alpha_mode = "STRAIGHT"
    return image


def _fill_image(image, color):
    count = image.size[0] * image.size[1]
    pixels = np.tile(np.array(color, dtype=np.float32), count)
    image.pixels.foreach_set(pixels)
    image.update()


def _grayscale_channel_from_image(image, size):
    src = image
    temp = None
    if tuple(src.size) != (size, size):
        temp = src.copy()
        temp.scale(size, size)
        src = temp
    count = src.size[0] * src.size[1]
    flat = np.empty(count * src.channels, dtype=np.float32)
    src.pixels.foreach_get(flat)
    gray = flat.reshape((count, src.channels))[:, 0].copy()
    if temp is not None:
        bpy.data.images.remove(temp)
    return gray


def _find_ao_image(base_name, directory):
    image = bpy.data.images.get(base_name + "_ao")
    if image is not None:
        return image
    filepath = os.path.join(directory, base_name + "_ao.png")
    if os.path.isfile(filepath):
        return bpy.data.images.load(filepath, check_existing=True)
    return None


def _save_png(image, directory):
    filepath = os.path.join(directory, image.name + ".png")
    image.filepath_raw = filepath
    image.file_format = "PNG"
    image.save()
    return filepath


class UVTT_OT_generate_base_tex(SaveFileMixin, bpy.types.Operator):
    """Generate a flat-fill base texture set for the selected mesh(es).

    A quick, correctly-named and correctly-packed starting point to paint
    over: "<mesh>_am.png" (RGB albedo), "<mesh>_maor.png" (RGBA - Metal in
    R, AO in G, Roughness in Alpha) and "<mesh>_nm.png" (flat tangent-space
    normal). If a "<mesh>_ao" image already exists (e.g. from Bake AO), it
    is used for the AO channel instead of the flat AO value.
    """

    bl_idname = "uvtt.generate_base_tex"
    bl_label = "Generate Base Tex"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(_checker_objects(context))

    def execute(self, context):
        if not self._ensure_saved(context):
            return {"CANCELLED"}

        scene = context.scene
        size = int(scene.uvtt_basetex_size)
        directory = os.path.dirname(bpy.data.filepath)
        albedo = tuple(scene.uvtt_basetex_albedo_color)
        metal_value = scene.uvtt_basetex_metal
        ao_value = scene.uvtt_basetex_ao
        roughness_value = scene.uvtt_basetex_roughness

        created = []
        for obj in _checker_objects(context):
            base_name = _safe_filename(obj.name)

            am_image = _get_or_new_image(base_name + "_am", size)
            am_image.colorspace_settings.name = "sRGB"
            _fill_image(am_image, albedo + (1.0,))
            _save_png(am_image, directory)

            maor_image = _get_or_new_image(base_name + "_maor", size, alpha=True)
            maor_image.colorspace_settings.name = "Non-Color"
            count = size * size
            ao_source = _find_ao_image(base_name, directory)
            g = (
                _grayscale_channel_from_image(ao_source, size)
                if ao_source is not None
                else np.full(count, ao_value, dtype=np.float32)
            )
            packed = np.empty(count * 4, dtype=np.float32)
            packed[0::4] = metal_value
            packed[1::4] = g
            packed[2::4] = 0.0
            packed[3::4] = roughness_value
            maor_image.pixels.foreach_set(packed)
            maor_image.update()
            _save_png(maor_image, directory)

            nm_image = _get_or_new_image(base_name + "_nm", size)
            nm_image.colorspace_settings.name = "Non-Color"
            _fill_image(nm_image, (0.5, 0.5, 1.0, 1.0))
            _save_png(nm_image, directory)

            created.append(base_name)

        self.report({"INFO"}, "Generated base textures for %d object(s)" % len(created))
        context.scene["uvtt_checker_tip"] = "Generated base textures: " + ", ".join(created)
        return {"FINISHED"}


class UVTT_PT_tools(bpy.types.Panel):
    bl_label = "Tools"
    bl_idname = "UVTT_PT_tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "TechArt Tools"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Export")
        box.prop(context.scene, "uvtt_export_use_mesh_name")
        row = box.row(align=True)
        row.operator("uvtt.export_fbx", text="Export FBX", icon="EXPORT")
        row.operator("uvtt.export_obj", text="Export OBJ", icon="EXPORT")
        if not bpy.data.filepath:
            box.label(text="Save the .blend file first", icon="ERROR")

        box = layout.box()
        box.label(text="Check Intersection")
        box.prop(context.scene, "uvtt_intersection_depth")
        row = box.row(align=True)
        row.operator("uvtt.check_intersection", text="Check", icon="VIEWZOOM")
        row.operator("uvtt.clean_intersection", text="Clean", icon="TRASH")

        box = layout.box()
        box.label(text="Render Preview")
        box.operator("uvtt.render_preview", text="Render Preview", icon="RENDER_STILL")
        if not bpy.data.filepath:
            box.label(text="Save the .blend file first", icon="ERROR")

        box = layout.box()
        box.label(text="Auto LOD")
        box.prop(context.scene, "uvtt_lod_ratio", text="Reduction per LOD")
        box.label(
            text="Each next LOD will be about %d%% of the previous."
            % round(context.scene.uvtt_lod_ratio * 100)
        )
        box.operator("uvtt.auto_lod", text="Generate LODs", icon="MOD_DECIM")


class UVTT_PT_checker_tips(bpy.types.Panel):
    bl_label = "Tips"
    bl_idname = "UVTT_PT_checker_tips"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "TechArt Tools"

    def draw(self, context):
        layout = self.layout

        col = layout.column(align=True)
        for line in operators.wrap_text_for_region(context, CHECKER_INTRO_TIP):
            col.label(text=line)

        layout.separator()

        col = layout.column(align=True)
        for line in operators.wrap_text_for_region(context, PREPARE_INTRO_TIP):
            col.label(text=line)

        summary = context.scene.get("uvtt_checker_tip")
        if summary:
            layout.separator()
            box = layout.box()
            box.label(text="Last Run", icon="INFO")
            col = box.column(align=True)
            for line in operators.wrap_text_for_region(context, summary):
                col.label(text=line)


classes = (
    UVTT_CheckResult,
    UVTT_OT_run_checker,
    UVTT_OT_fix_units,
    UVTT_OT_fix_pivot_origin,
    UVTT_OT_fix_pivot_bbox,
    UVTT_OT_fix_hidden,
    UVTT_OT_fix_backface_cull,
    UVTT_OT_fix_transform,
    UVTT_OT_fix_polygons,
    UVTT_OT_fix_materials,
    UVTT_OT_prepare_mesh,
    UVTT_OT_prepare_scene,
    UVTT_OT_open_checker_log,
    UVTT_OT_get_statistics,
    UVTT_OT_toggle_dimensions,
    UVTT_OT_export_fbx,
    UVTT_OT_export_obj,
    UVTT_OT_check_intersection,
    UVTT_OT_clean_intersection,
    UVTT_OT_render_preview,
    UVTT_OT_auto_lod,
    UVTT_OT_bake_ao,
    UVTT_OT_generate_base_tex,
    UVTT_PT_viewport_guide,
    UVTT_PT_prepare,
    UVTT_PT_statistics,
    UVTT_PT_material,
    UVTT_PT_checker,
    UVTT_PT_tools,
    UVTT_PT_checker_tips,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.uvtt_check_results = bpy.props.CollectionProperty(type=UVTT_CheckResult)
    bpy.types.Scene.uvtt_export_use_mesh_name = bpy.props.BoolProperty(
        name="Use mesh name as file name",
        description="Name the exported file after the selected mesh instead of the .blend file",
        default=False,
    )
    bpy.types.Scene.uvtt_intersection_depth = bpy.props.IntProperty(
        name="Depth (mm)",
        description="Thickness of the red tube drawn along open (boundary) edges",
        default=10,
        min=1,
        max=99,
    )
    bpy.types.Scene.uvtt_lod_ratio = bpy.props.FloatProperty(
        name="Reduction per LOD",
        description="Target face ratio for each LOD relative to the previous one",
        default=0.5,
        min=0.05,
        max=0.95,
        precision=2,
        subtype="FACTOR",
    )
    bpy.types.Scene.uvtt_bake_ao_size = bpy.props.EnumProperty(
        name="Map Size",
        description="Resolution of the baked AO texture",
        items=operators.MAP_SIZES,
        default="1024",
    )
    bpy.types.Scene.uvtt_bake_ao_samples = bpy.props.IntProperty(
        name="Samples",
        description="Cycles samples used for the AO bake - higher is cleaner but slower",
        default=256,
        min=8,
        max=4096,
    )
    bpy.types.Scene.uvtt_bake_ao_margin = bpy.props.IntProperty(
        name="Margin (px)",
        description="Pixels of dilation past each UV island's edge, to avoid black seams",
        default=16,
        min=0,
        max=64,
    )
    bpy.types.Scene.uvtt_bake_ao_denoise = bpy.props.BoolProperty(
        name="Denoise",
        description="Denoise the bake result",
        default=True,
    )
    bpy.types.Scene.uvtt_basetex_size = bpy.props.EnumProperty(
        name="Map Size",
        description="Resolution of the generated base textures",
        items=operators.MAP_SIZES,
        default="1024",
    )
    bpy.types.Scene.uvtt_basetex_albedo_color = bpy.props.FloatVectorProperty(
        name="Albedo",
        description="Flat fill color for the albedo texture",
        subtype="COLOR",
        size=3,
        default=(0.5, 0.5, 0.5),
        min=0.0,
        max=1.0,
    )
    bpy.types.Scene.uvtt_basetex_metal = bpy.props.FloatProperty(
        name="Metal",
        description="Flat fill value for the Metal channel (R) of the MAOR texture",
        default=0.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    bpy.types.Scene.uvtt_basetex_ao = bpy.props.FloatProperty(
        name="AO",
        description="Flat fill value for the AO channel (G) of the MAOR texture - "
        "ignored if a matching Bake AO image already exists",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    bpy.types.Scene.uvtt_basetex_roughness = bpy.props.FloatProperty(
        name="Roughness",
        description="Flat fill value for the Roughness channel (Alpha) of the MAOR texture",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )


def unregister():
    _remove_dimension_handlers()

    del bpy.types.Scene.uvtt_basetex_roughness
    del bpy.types.Scene.uvtt_basetex_ao
    del bpy.types.Scene.uvtt_basetex_metal
    del bpy.types.Scene.uvtt_basetex_albedo_color
    del bpy.types.Scene.uvtt_basetex_size
    del bpy.types.Scene.uvtt_bake_ao_denoise
    del bpy.types.Scene.uvtt_bake_ao_margin
    del bpy.types.Scene.uvtt_bake_ao_samples
    del bpy.types.Scene.uvtt_bake_ao_size
    del bpy.types.Scene.uvtt_lod_ratio
    del bpy.types.Scene.uvtt_intersection_depth
    del bpy.types.Scene.uvtt_export_use_mesh_name
    del bpy.types.Scene.uvtt_check_results

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
