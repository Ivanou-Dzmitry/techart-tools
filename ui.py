import bpy


class UVTT_PT_main(bpy.types.Panel):
    bl_label = "UV Tech Tools"
    bl_idname = "UVTT_PT_main"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "UV Tech Tools"

    def draw(self, context):
        layout = self.layout
        layout.operator("uvtt.hello")

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
        box.label(text="Align")
        row = box.row(align=True)
        row.operator("uvtt.align", text="V-align").axis = "X"
        row.operator("uvtt.align", text="H-align").axis = "Y"

        box = layout.box()
        box.label(text="Checkers")
        col = box.column(align=True)
        row = col.row(align=True)
        row.operator("uvtt.set_checker", text="Standard").checker = "STANDARD"
        row.operator("uvtt.set_checker", text="Diagonal").checker = "DIAGONAL"
        row = col.row(align=True)
        row.operator("uvtt.set_checker", text="Digital").checker = "DIGITAL"
        row.operator("uvtt.set_checker", text="Gradient").checker = "GRADIENT"

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
        box.label(text="Material")
        box.operator("uvtt.reset_material", text="Reset")


classes = (UVTT_PT_main,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
