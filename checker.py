import os
import textwrap

import bmesh
import bpy
from mathutils import Vector

from . import operators

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


class UVTT_OT_fix_polygons(bpy.types.Operator):
    """Triangulate n-gons on the selected object(s). This changes topology"""

    bl_idname = "uvtt.fix_polygons"
    bl_label = "Fix Polygons"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
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


class UVTT_PT_checker(bpy.types.Panel):
    bl_label = "TechArt Tools - Checker"
    bl_idname = "UVTT_PT_checker"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "TechArt Tools"

    def draw(self, context):
        layout = self.layout
        layout.operator("wm.url_open", text="TechArt Tools Online Guide", icon="URL").url = (
            operators.TECHART_URL
        )
        layout.operator("uvtt.run_checker", text="Run Check")

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
            for line in textwrap.wrap(item.message, width=40):
                col.label(text=line)
            if item.fix_id:
                box.operator(item.fix_id, text="Fix")


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
    UVTT_PT_checker,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.uvtt_check_results = bpy.props.CollectionProperty(type=UVTT_CheckResult)


def unregister():
    del bpy.types.Scene.uvtt_check_results

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
