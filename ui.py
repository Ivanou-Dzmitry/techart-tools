import os
import textwrap

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


class UVTT_PT_main(bpy.types.Panel):
    bl_label = "TechArt Tools"
    bl_idname = "UVTT_PT_main"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "TechArt Tools"

    def draw(self, context):
        layout = self.layout
        layout.operator("wm.url_open", text="TechArt Tools Online Guide", icon="URL").url = (
            operators.TECHART_URL
        )

        box = layout.box()
        box.label(text="Rotate")
        row = box.row(align=True)
        row.operator("uvtt.rotate", text="-90").angle = -90.0
        row.operator("uvtt.rotate", text="-45").angle = -45.0
        row.operator("uvtt.rotate", text="+45").angle = 45.0
        row.operator("uvtt.rotate", text="+90").angle = 90.0

        box = layout.box()
        box.label(text="Scale")
        row = box.row(align=True)
        row.operator("uvtt.scale", text="x0.25").factor = 0.25
        row.operator("uvtt.scale", text="x0.5").factor = 0.5
        row.operator("uvtt.scale", text="x2").factor = 2.0
        row.operator("uvtt.scale", text="4").factor = 4.0

        box = layout.box()
        box.label(text="Move UV")
        row = box.row(align=True)
        row.operator("uvtt.move", text="-1U").offset_u = -1.0
        row.operator("uvtt.move", text="+1U").offset_u = 1.0
        row.operator("uvtt.move", text="-1V").offset_v = -1.0
        row.operator("uvtt.move", text="+1V").offset_v = 1.0

        box = layout.box()
        box.label(text="Align")
        row = box.row(align=True)
        row.operator("uvtt.align", text="V-align").axis = "X"
        row.operator("uvtt.align", text="H-align").axis = "Y"

        box = layout.box()
        box.label(text="Checkers")
        box.template_icon_view(
            context.scene, "uvtt_checker_preview", show_labels=True, scale=5.0
        )

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
        box.operator("uvtt.render_uv", text="Render")

        box = layout.box()
        box.label(text="Material")
        row = box.row(align=True)
        row.operator("uvtt.set_gloss", text="Gloss")
        row.operator("uvtt.set_matte", text="Matte")
        row.operator("uvtt.set_normal_check", text="NM")
        box.operator("uvtt.reset_material", text="Reset")

        box = layout.box()
        box.label(text="UV Utilization")
        box.operator("uvtt.check_uv_utilization", text="Check")
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
        row.operator("uvtt.get_texel", text="Get Texel")
        box.prop(context.scene, "uvtt_texel_value", text="Texel (px/m)")
        box.prop(context.scene, "uvtt_use_measured_texel")

        box = layout.box()
        box.label(text="Set Texel Density")
        box.label(text="Map size: %spx" % context.scene.uvtt_map_size)
        box.label(text="Desired texel (px/m)")
        row = box.row(align=True)
        row.prop(context.scene, "uvtt_desired_texel", text="")
        row.operator("uvtt.set_texel", text="Set Texel")

        box = layout.box()
        box.label(text="Check Texel Density")
        box.label(text="Using Texel: %d px/m" % int(context.scene.uvtt_texel_value))
        box.prop(context.scene, "uvtt_texel_range")
        row = box.row(align=True)
        row.operator("uvtt.check_texel", text="Check Texel")
        row.operator("uvtt.clean_texel_check", text="Clean Check")

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
        )

        tip = context.scene.get("uvtt_tip")
        if tip:
            box = layout.box()
            box.label(text="Tips", icon="INFO")
            col = box.column(align=True)
            for line in textwrap.wrap(tip, width=40):
                col.label(text=line)


classes = (UVTT_PT_main,)


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
