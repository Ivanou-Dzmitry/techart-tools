import bpy


class UVTT_OT_hello(bpy.types.Operator):
    """Sanity-check operator, confirms the addon is registered and running"""

    bl_idname = "uvtt.hello"
    bl_label = "UV Tech Tools: Hello"
    bl_options = {"REGISTER"}

    def execute(self, context):
        self.report({"INFO"}, "UV Tech Tools is installed and working")
        return {"FINISHED"}


classes = (UVTT_OT_hello,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
