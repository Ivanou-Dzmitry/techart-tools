import bpy

from . import operators
from . import checker
from . import ui

modules = (operators, checker, ui)


def register():
    for module in modules:
        module.register()


def unregister():
    for module in reversed(modules):
        module.unregister()
