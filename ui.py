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


classes = (UVTT_PT_main,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
