import os

import bpy
import bpy.utils.previews

from . import operators

ICON_DIR = os.path.join(os.path.dirname(__file__), "icons")

CHECKER_ICON_FILES = {
    "STANDARD": "checker_standard.png",
    "DIAGONAL": "checker_diagonal.png",
    "DIGITAL": "checker_digital.png",
    "GRADIENT": "checker_gradient.png",
}

_icon_collections = {}
_checker_enum_items = []


def _get_checker_enum_items(self, context):
    return _checker_enum_items


def _on_checker_preview_change(self, context):
    bpy.ops.uvtt.set_checker(checker=self.uvtt_checker_preview)


class UVTT_PT_guide(bpy.types.Panel):
    bl_label = "TechArt Tools"
    bl_idname = "UVTT_PT_guide"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "TechArt Tools"

    def draw(self, context):
        self.bl_label = "TechArt Tools v%s" % operators.TECHART_VERSION
        layout = self.layout
        layout.operator("wm.url_open", text="TechArt Tools Online Guide", icon="URL").url = (
            operators.TECHART_URL
        )


class UVTT_PT_uv_manipulation(bpy.types.Panel):
    bl_label = "UV Manipulation"
    bl_idname = "UVTT_PT_uv_manipulation"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "TechArt Tools"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="Auto UV", icon="MOD_UVPROJECT")
        box.prop(context.scene, "uvtt_auto_uv_margin")
        box.operator("uvtt.auto_uv", text="Unwrap", icon="UV")

        box = layout.box()
        box.label(text="Rotate", icon="CON_ROTLIKE")
        grid = box.grid_flow(columns=2, align=True)
        grid.operator("uvtt.rotate", text="CW 90°").angle = -90.0
        grid.operator("uvtt.rotate", text="CW 45°").angle = -45.0
        grid.operator("uvtt.rotate", text="CCW 45°").angle = 45.0
        grid.operator("uvtt.rotate", text="CCW 90°").angle = 90.0

        box = layout.box()
        box.label(text="Scale", icon="CON_SIZELIKE")
        grid = box.grid_flow(columns=4, align=True)
        grid.operator("uvtt.scale", text="x0.25").factor = 0.25
        grid.operator("uvtt.scale", text="x0.5").factor = 0.5
        grid.operator("uvtt.scale", text="x2").factor = 2.0
        grid.operator("uvtt.scale", text="4").factor = 4.0

        box = layout.box()
        box.label(text="Move UV", icon="CON_LOCLIKE")
        grid = box.grid_flow(columns=4, align=True)
        grid.operator("uvtt.move", text="-1U", icon="TRIA_LEFT").offset_u = -1.0
        grid.operator("uvtt.move", text="+1U", icon="TRIA_RIGHT").offset_u = 1.0
        grid.operator("uvtt.move", text="-1V", icon="TRIA_DOWN").offset_v = -1.0
        grid.operator("uvtt.move", text="+1V", icon="TRIA_UP").offset_v = 1.0

        box = layout.box()
        box.label(text="Flip", icon="MOD_MIRROR")
        row = box.row(align=True)
        row.operator("uvtt.flip", text="Flip U", icon="TRIA_LEFT").axis = "U"
        row.operator("uvtt.flip", text="Flip V", icon="TRIA_DOWN").axis = "V"

        box = layout.box()
        box.label(text="Align", icon="OBJECT_ORIGIN")
        row = box.row(align=True)
        row.operator("uvtt.align", text="V-align").axis = "X"
        row.operator("uvtt.align", text="H-align").axis = "Y"


class UVTT_PT_checkers(bpy.types.Panel):
    bl_label = "Checkers"
    bl_idname = "UVTT_PT_checkers"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "TechArt Tools"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="Checkers", icon="TEXTURE")
        box.template_icon_view(
            context.scene, "uvtt_checker_preview", show_labels=True, scale=5.0
        )
        box.operator("uvtt.reset_material", text="Remove Checker", icon="LOOP_BACK")

        box.label(text="Texture Size")
        col = box.column(align=True)
        row = col.row(align=True)
        row.operator("uvtt.set_checker_size", text="128").size = 128
        row.operator("uvtt.set_checker_size", text="256").size = 256
        row.operator("uvtt.set_checker_size", text="512").size = 512
        row.operator("uvtt.set_checker_size", text="1K").size = 1024
        row = col.row(align=True)
        row.operator("uvtt.set_checker_size", text="2K").size = 2048
        row.operator("uvtt.set_checker_size", text="4K").size = 4096
        row.operator("uvtt.set_checker_size", text="8K").size = 8192

        box = layout.box()
        box.label(text="Render UV")
        box.prop(context.scene, "uvtt_render_uv_size", text="Map Size (px)")
        box.prop(context.scene, "uvtt_render_uv_opacity")
        box.operator("uvtt.render_uv", text="Render", icon="RENDER_STILL")
        if not bpy.data.filepath:
            box.label(text="Save the .blend file first", icon="ERROR")


class UVTT_PT_texel(bpy.types.Panel):
    bl_label = "Texel"
    bl_idname = "UVTT_PT_texel"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "TechArt Tools"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="UV Utilization", icon="UV")
        box.operator("uvtt.check_uv_utilization", text="Check", icon="VIEWZOOM")
        utilization = context.scene.get("uvtt_uv_utilization")
        if utilization is not None:
            box.label(text="Coverage: %.1f%%" % utilization)
            image_name = context.scene.get("uvtt_uv_utilization_image")
            image = bpy.data.images.get(image_name) if image_name else None
            if image is not None:
                box.template_icon(icon_value=image.preview.icon_id, scale=5.0)

        box = layout.box()
        box.label(text="Get Texel Density")
        box.label(text="Map size (px)")
        row = box.row(align=True)
        row.prop(context.scene, "uvtt_map_size", text="")
        row.operator("uvtt.get_texel", text="Get Texel", icon="VIEWZOOM")
        box.prop(context.scene, "uvtt_texel_value", text="Texel (px/m)")
        box.prop(context.scene, "uvtt_use_measured_texel")

        box = layout.box()
        box.label(text="Set Texel Density")
        box.label(text="Map size: %spx" % context.scene.uvtt_map_size)
        box.prop(context.scene, "uvtt_texel_set_method", text="Mode")
        box.prop(context.scene, "uvtt_texel_scale_anchor", text="Scale Anchor (UV)")
        box.label(text="Desired texel (px/m)")
        row = box.row(align=True)
        row.prop(context.scene, "uvtt_desired_texel", text="")
        row.operator("uvtt.set_texel", text="Set Texel", icon="CON_SIZELIKE")

        box = layout.box()
        box.label(text="Check Texel Density")
        box.label(text="Using Texel: %d px/m" % int(context.scene.uvtt_texel_value))
        box.prop(context.scene, "uvtt_texel_range")
        row = box.row(align=True)
        row.operator("uvtt.check_texel", text="Check Texel", icon="CHECKMARK")
        row.operator("uvtt.clean_texel_check", text="Clean Check", icon="TRASH")

        in_range = context.scene.get("uvtt_texel_inrange")
        if in_range is not None:
            box.label(
                text="In-range: %d   Stretched: %d   Compressed: %d"
                % (
                    in_range,
                    context.scene.get("uvtt_texel_stretched", 0),
                    context.scene.get("uvtt_texel_compressed", 0),
                )
            )

        box.separator()
        box.label(text="Additional Checks")

        box.label(text="Tiny Polygons (m2)")
        row = box.row(align=True)
        row.prop(context.scene, "uvtt_tiny_poly_area", text="")
        tiny_poly_count = context.scene.get("uvtt_tiny_poly_count")
        sub = row.row()
        sub.enabled = bool(tiny_poly_count)
        sub.operator(
            "uvtt.select_tiny_polygons",
            text=("Select (%d)" % tiny_poly_count) if tiny_poly_count else "Not checked",
            icon="RESTRICT_SELECT_OFF",
        )

        box.label(text="Tiny UV Shells (px)")
        row = box.row(align=True)
        row.prop(context.scene, "uvtt_tiny_uv_px", text="")
        tiny_uv_count = context.scene.get("uvtt_tiny_uv_count")
        sub = row.row()
        sub.enabled = bool(tiny_uv_count)
        sub.operator(
            "uvtt.select_tiny_uv",
            text=("Select (%d)" % tiny_uv_count) if tiny_uv_count else "Not checked",
            icon="RESTRICT_SELECT_OFF",
        )


class UVTT_PT_uv_tools(bpy.types.Panel):
    bl_label = "Tools"
    bl_idname = "UVTT_PT_uv_tools"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "TechArt Tools"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="Export UV Layout", icon="EXPORT")
        box.prop(context.scene, "uvtt_export_uv_size", text="Map Size (px)")
        box.operator("uvtt.export_uv_layout", text="Export UV", icon="EXPORT")
        if not bpy.data.filepath:
            box.label(text="Save the .blend file first", icon="ERROR")


class UVTT_PT_tips(bpy.types.Panel):
    bl_label = "Tips"
    bl_idname = "UVTT_PT_tips"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "TechArt Tools"

    @classmethod
    def poll(cls, context):
        return bool(context.scene.get("uvtt_tip"))

    def draw(self, context):
        layout = self.layout
        tip = context.scene.get("uvtt_tip")
        col = layout.column(align=True)
        for line in operators.wrap_text_for_region(context, tip):
            col.label(text=line)


classes = (
    UVTT_PT_guide,
    UVTT_PT_uv_manipulation,
    UVTT_PT_checkers,
    UVTT_PT_texel,
    UVTT_PT_uv_tools,
    UVTT_PT_tips,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    pcoll = bpy.utils.previews.new()
    _checker_enum_items.clear()
    for index, (cid, label, _filename) in enumerate(operators.CHECKERS):
        icon_filename = CHECKER_ICON_FILES[cid]
        pcoll.load(cid, os.path.join(ICON_DIR, icon_filename), "IMAGE")
        _checker_enum_items.append((cid, label, "", pcoll[cid].icon_id, index))
    _icon_collections["main"] = pcoll

    bpy.types.Scene.uvtt_checker_preview = bpy.props.EnumProperty(
        name="Checker",
        items=_get_checker_enum_items,
        update=_on_checker_preview_change,
    )


def unregister():
    del bpy.types.Scene.uvtt_checker_preview

    for pcoll in _icon_collections.values():
        bpy.utils.previews.remove(pcoll)
    _icon_collections.clear()
    _checker_enum_items.clear()

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
