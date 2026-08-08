import math
import os

import bmesh
import bpy
from mathutils import Vector

CHECKER_DIR = os.path.join(os.path.dirname(__file__), "checkers")

CHECKERS = (
    ("STANDARD", "Standard", "checker_standard.tga"),
    ("DIAGONAL", "Diagonal", "checker_diagonal.tga"),
    ("DIGITAL", "Digital", "checker_digital.tga"),
    ("GRADIENT", "Gradient", "checker_gradient.tga"),
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
            pivot = _uv_pivot(uv_layer, loops)

            for loop in loops:
                luv = loop[uv_layer]
                luv.uv = pivot + (luv.uv - pivot) * self.factor

            bmesh.update_edit_mesh(obj.data)

        if not any_selected:
            self.report({"WARNING"}, "No UVs selected")
            return {"CANCELLED"}

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

        objects = context.selected_objects or [context.edit_object]
        applied = False
        for obj in objects:
            if obj.type != "MESH":
                continue
            if obj.data.materials:
                obj.data.materials[0] = mat
            else:
                obj.data.materials.append(mat)
            applied = True

        if context.area is not None and context.area.type == "IMAGE_EDITOR":
            context.area.spaces.active.image = image

        if not applied:
            self.report({"WARNING"}, "No mesh objects selected")
            return {"CANCELLED"}

        _set_material_preview_shading(context)

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
                applied = True

        if not applied:
            if skipped_gradient:
                self.report({"INFO"}, "Gradient checker does not support texture size")
            else:
                self.report({"WARNING"}, "No checker material found on selection")
            return {"CANCELLED"}

        return {"FINISHED"}


class UVTT_OT_reset_material(bpy.types.Operator):
    """Remove all materials from the selected objects"""

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
            obj.data.materials.clear()
            applied = True

        if not applied:
            self.report({"WARNING"}, "No mesh objects selected")
            return {"CANCELLED"}

        return {"FINISHED"}


classes = (
    UVTT_OT_hello,
    UVTT_OT_rotate,
    UVTT_OT_scale,
    UVTT_OT_align,
    UVTT_OT_set_checker,
    UVTT_OT_set_checker_size,
    UVTT_OT_reset_material,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
