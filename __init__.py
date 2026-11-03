# SPDX-License-Identifier: GPL-3.0-or-later
"""View Notes: pin a note to the current viewport view.

Each note stores the exact view (view and projection matrices, lens, shading mode), the selection, the
annotation strokes drawn for it and a screenshot of the viewport, in a JSON file next to the .blend. A
reviewer, a teammate or an AI agent can then reproduce the view and render it on a newer version of the
model. The file format is documented in README.md.
"""

import datetime
import json
import os

import bmesh
import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty
from mathutils import Vector

NOTES_FILE = "notes.json"
FORMAT_VERSION = 1
MAX_INDICES = 20000  # per object; the centre and bounds are always stored

_cache = {"path": None, "mtime": None, "data": None}


# --- storage -------------------------------------------------------------------------------------------------


def _prefs(context):
    return context.preferences.addons[__package__].preferences


def notes_dir(context):
    """Absolute notes folder for the open .blend, or None while the file is unsaved."""
    if not bpy.data.filepath:
        return None
    return os.path.normpath(bpy.path.abspath(_prefs(context).folder))


def load_notes(folder):
    path = os.path.join(folder, NOTES_FILE)
    if not os.path.exists(path):
        return {"format": FORMAT_VERSION, "notes": []}
    mtime = os.path.getmtime(path)
    if _cache["path"] == path and _cache["mtime"] == mtime:
        return _cache["data"]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    _cache.update(path=path, mtime=mtime, data=data)
    return data


def save_notes(folder, data):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, NOTES_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)
    _cache.update(path=None, mtime=None, data=None)


def _m(m):
    return [list(r) for r in m]


def _v(v):
    return [round(x, 6) for x in v]


# --- capture -------------------------------------------------------------------------------------------------


def find_view(context):
    """(window, area, region, space, rv3d) of the 3D view the note is about."""
    window = context.window
    area = context.area if context.area and context.area.type == "VIEW_3D" else None
    if area is None:
        areas = [a for a in window.screen.areas if a.type == "VIEW_3D"]
        if not areas:
            return None
        area = max(areas, key=lambda a: a.width * a.height)
    space = area.spaces.active
    rv3d = space.region_3d
    regions = [r for r in area.regions if r.type == "WINDOW"]
    region = next((r for r in regions if r.data == rv3d), regions[-1])
    return window, area, region, space, rv3d


def view_info(region, space, rv3d):
    w, h = region.width, region.height
    P = rv3d.window_matrix
    info = {
        "perspective": rv3d.view_perspective,  # PERSP, ORTHO or CAMERA
        "is_perspective": rv3d.is_perspective,
        "view_matrix": _m(rv3d.view_matrix),  # world to view (camera looks down -Z)
        "window_matrix": _m(P),  # view to clip (OpenGL convention)
        "eye": _v(rv3d.view_matrix.inverted().translation),
        "view_location": _v(rv3d.view_location),  # the point the view orbits around
        "view_rotation": _v(rv3d.view_rotation),  # quaternion w, x, y, z
        "view_distance": rv3d.view_distance,
        "lens": space.lens,
        "clip_start": space.clip_start,
        "clip_end": space.clip_end,
        "region_size": [w, h],
    }
    if rv3d.is_perspective:
        import math

        info["fov_x_deg"] = math.degrees(2 * math.atan(1 / P[0][0]))
        info["fov_y_deg"] = math.degrees(2 * math.atan(1 / P[1][1]))
    else:
        info["ortho_width"] = 2 / P[0][0]
        info["ortho_height"] = 2 / P[1][1]
    return info


def shading_info(space):
    sh, ov = space.shading, space.overlay
    info = {
        "type": sh.type,  # WIREFRAME, SOLID, MATERIAL, RENDERED
        "wireframe_overlay": bool(ov.show_wireframes),
        "xray": bool(sh.show_xray),
        "light": sh.light,
        "color_type": sh.color_type,
        "backface_culling": bool(sh.show_backface_culling),
    }
    if sh.type == "MATERIAL":
        info["studio_light"] = sh.studio_light
        info["use_scene_world"] = bool(sh.use_scene_world)
    return info


def _bounds(points):
    if not points:
        return None
    lo = Vector(tuple(min(p[i] for p in points) for i in range(3)))
    hi = Vector(tuple(max(p[i] for p in points) for i in range(3)))
    return {"center": _v((lo + hi) / 2), "min": _v(lo), "max": _v(hi)}


def selection_info(context):
    obj = context.active_object
    info = {
        "mode": context.mode,
        "active_object": obj.name if obj else None,
        "selected_objects": [o.name for o in context.selected_objects],
    }
    if context.mode == "EDIT_MESH":
        sel = []
        for o in context.objects_in_mode_unique_data:
            bm = bmesh.from_edit_mesh(o.data)
            bm.verts.index_update()
            bm.faces.index_update()
            mw = o.matrix_world
            verts = [v for v in bm.verts if v.select]
            if not verts:
                continue
            faces = [f.index for f in bm.faces if f.select]
            entry = {
                "object": o.name,
                "vertex_count": len(verts),
                "face_count": len(faces),
                "vertices": [v.index for v in verts[:MAX_INDICES]],
                "faces": faces[:MAX_INDICES],
            }
            entry.update(_bounds([mw @ v.co for v in verts]))
            sel.append(entry)
        info["edit_selection"] = sel
    elif context.selected_objects:
        pts = []
        for o in context.selected_objects:
            pts += [o.matrix_world @ Vector(c) for c in o.bound_box]
        info["selected_bounds"] = _bounds(pts)
    return info


def annotation_info(context, clear):
    """The visible annotation strokes on the current frame; cleared afterwards so each note keeps its own."""
    gp = context.scene.annotation
    out = []
    if gp is None:
        return out
    for layer in gp.layers:
        if layer.annotation_hide:
            continue
        frame = layer.active_frame
        if frame is None:
            continue
        for s in frame.strokes:
            out.append({"layer": layer.info, "color": _v(layer.color), "points": [_v(p.co) for p in s.points]})
        if clear and len(frame.strokes):
            layer.frames.remove(frame)
    return out


_UI_REGIONS = ("show_region_ui", "show_region_toolbar", "show_region_header", "show_region_tool_header")


def hide_ui(space):
    """Hide the panels drawn over the viewport; returns what to restore. Takes effect at the next redraw."""
    saved = {k: getattr(space, k) for k in _UI_REGIONS}
    saved["show_gizmo_navigate"] = space.show_gizmo_navigate
    for k in _UI_REGIONS:
        setattr(space, k, False)
    space.show_gizmo_navigate = False
    return saved


def restore_ui(space, saved):
    for k, v in saved.items():
        setattr(space, k, v)


def screenshot(window, area, region, path):
    """Screenshot of the viewport region, cropped to the region the view's projection covers."""
    tmp = path + ".area.png"
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.screen.screenshot_area(filepath=tmp, hide_props_region=True, check_existing=False)
    img = bpy.data.images.load(tmp, check_existing=False)
    try:
        iw, ih = img.size
        sx, sy = iw / area.width, ih / area.height
        x0 = max(0, round((region.x - area.x) * sx))
        y0 = max(0, round((region.y - area.y) * sy))
        w = min(iw - x0, round(region.width * sx))
        h = min(ih - y0, round(region.height * sy))
        import numpy as np

        px = np.empty(iw * ih * 4, np.float32)
        img.pixels.foreach_get(px)
        crop = px.reshape(ih, iw, 4)[y0 : y0 + h, x0 : x0 + w].copy()
        crop[..., 3] = 1.0
        out = bpy.data.images.new("view_note_shot", w, h, alpha=False)
        out.pixels.foreach_set(crop.ravel())
        out.filepath_raw = path
        out.file_format = "PNG"
        out.save()
        bpy.data.images.remove(out)
    finally:
        bpy.data.images.remove(img)
        os.remove(tmp)


# --- operators -----------------------------------------------------------------------------------------------


class VIEWNOTES_OT_add(bpy.types.Operator):
    """Save a note with the current view, selection, annotations and a screenshot"""

    bl_idname = "view_notes.add"
    bl_label = "Add View Note"
    bl_options = {"REGISTER"}

    text: StringProperty(name="Note", description="What is wrong, or what you want")

    @classmethod
    def poll(cls, context):
        return context.window is not None

    def invoke(self, context, event):
        if self.text.strip():
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self, width=560)

    def draw(self, context):
        self.layout.activate_init = True  # type straight away, no click into the field
        self.layout.prop(self, "text", text="")

    def execute(self, context):
        folder = notes_dir(context)
        if folder is None:
            self.report({"ERROR"}, "Save the .blend first: notes are stored next to it")
            return {"CANCELLED"}
        found = find_view(context)
        if found is None:
            self.report({"ERROR"}, "No 3D viewport in this window")
            return {"CANCELLED"}
        window, area, region, space, rv3d = found
        prefs = _prefs(context)
        text = self.text.strip()
        if context.scene.view_notes_text == self.text:
            context.scene.view_notes_text = ""
        saved = hide_ui(space) if prefs.hide_panels else None

        # The capture waits for the next redraw, so the dialog has closed and the hidden panels are gone.
        def capture():
            try:
                with bpy.context.temp_override(window=window, area=area, region=region):
                    nid = write_note(bpy.context, folder, text, window, area, region, space, rv3d, prefs)
                print(f"View Notes: note {nid} saved to {folder}")
            except Exception:
                import traceback

                traceback.print_exc()
            finally:
                if saved:
                    restore_ui(space, saved)
                for a in window.screen.areas:
                    a.tag_redraw()
            return None

        bpy.app.timers.register(capture, first_interval=0.15)
        return {"FINISHED"}


def write_note(context, folder, text, window, area, region, space, rv3d, prefs):
    os.makedirs(folder, exist_ok=True)
    data = load_notes(folder)
    nid = max((n["id"] for n in data["notes"]), default=0) + 1
    shot = f"note_{nid:03d}.png"
    screenshot(window, area, region, os.path.join(folder, shot))
    note = {
        "id": nid,
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "author": prefs.author,
        "blend": bpy.path.basename(bpy.data.filepath),
        "text": text,
        "status": "open",
        "resolution": "",
        "screenshot": shot,
        "frame": context.scene.frame_current,
        "view": view_info(region, space, rv3d),
        "shading": shading_info(space),
        "selection": selection_info(context),
        "cursor": _v(context.scene.cursor.location),
        "annotations": annotation_info(context, prefs.clear_annotations),
    }
    data = dict(data)
    data["notes"] = list(data["notes"]) + [note]
    save_notes(folder, data)
    return nid


def _stroke_count(scene):
    gp = scene.annotation
    if gp is None:
        return 0
    return sum(len(l.active_frame.strokes) for l in gp.layers if l.active_frame)


class VIEWNOTES_OT_draw(bpy.types.Operator):
    """Draw on the model to point at things, then write the note (Enter) or cancel (Esc)"""

    bl_idname = "view_notes.draw"
    bl_label = "New View Note"
    bl_options = {"REGISTER"}

    HINT = "View Note: draw with the left mouse button to point at things (Ctrl+Z undoes a stroke). " \
           "Enter: write the note.  Esc: cancel."

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def invoke(self, context, event):
        if not bpy.data.filepath:
            self.report({"ERROR"}, "Save the .blend first: notes are stored next to it")
            return {"CANCELLED"}
        ts = context.tool_settings
        self._area = context.area
        self._placement = ts.annotation_stroke_placement_view3d
        tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
        self._tool = tool.idname if tool else "builtin.select_box"
        self._strokes = _stroke_count(context.scene)
        if context.scene.annotation is None:  # a layer that reads in screenshots; existing ones are left alone
            gp = bpy.data.annotations.new("View Notes")
            layer = gp.layers.new("Note", set_active=True)
            layer.color = (1.0, 0.1, 0.05)
            layer.thickness = 6
            context.scene.annotation = gp
        ts.annotation_stroke_placement_view3d = "SURFACE"
        bpy.ops.wm.tool_set_by_id(name="builtin.annotate")
        self._area.header_text_set(self.HINT)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _restore(self, context):
        context.tool_settings.annotation_stroke_placement_view3d = self._placement
        with context.temp_override(area=self._area):
            bpy.ops.wm.tool_set_by_id(name=self._tool)
        self._area.header_text_set(None)

    def modal(self, context, event):
        if event.type in ("RET", "NUMPAD_ENTER"):
            # Open the dialog on release: opened on press, the release that follows confirms it empty.
            if event.value != "RELEASE":
                return {"RUNNING_MODAL"}
            self._restore(context)
            region = next(r for r in self._area.regions if r.type == "WINDOW")
            with context.temp_override(area=self._area, region=region):
                bpy.ops.view_notes.add("INVOKE_DEFAULT", text=context.scene.view_notes_text)
            return {"FINISHED"}
        if event.type == "ESC" and event.value == "PRESS":
            self._restore(context)
            gp = context.scene.annotation
            if gp is not None and self._strokes == 0:  # only what was drawn for this note
                for layer in gp.layers:
                    if layer.active_frame is not None and len(layer.active_frame.strokes):
                        layer.frames.remove(layer.active_frame)
            self._area.tag_redraw()
            return {"CANCELLED"}
        return {"PASS_THROUGH"}


def _edit_note(context, nid, fn):
    folder = notes_dir(context)
    data = load_notes(folder)
    notes = [dict(n) for n in data["notes"]]
    for n in notes:
        if n["id"] == nid:
            if fn(n) is False:
                notes.remove(n)
            break
    save_notes(folder, dict(data, notes=notes))


class VIEWNOTES_OT_goto(bpy.types.Operator):
    """Show this note's view and shading in the viewport"""

    bl_idname = "view_notes.goto"
    bl_label = "Go to Note View"
    note_id: IntProperty()

    def execute(self, context):
        found = find_view(context)
        if found is None:
            return {"CANCELLED"}
        _w, _a, _r, space, rv3d = found
        note = next((n for n in load_notes(notes_dir(context))["notes"] if n["id"] == self.note_id), None)
        if note is None:
            return {"CANCELLED"}
        v = note["view"]
        rv3d.view_perspective = "PERSP" if v["perspective"] == "CAMERA" else v["perspective"]
        rv3d.view_location = v["view_location"]
        rv3d.view_rotation = v["view_rotation"]
        rv3d.view_distance = v["view_distance"]
        space.lens = v["lens"]
        if v["perspective"] == "CAMERA" and context.scene.camera:
            rv3d.view_perspective = "CAMERA"
        space.shading.type = note["shading"]["type"]
        space.overlay.show_wireframes = note["shading"]["wireframe_overlay"]
        return {"FINISHED"}


class VIEWNOTES_OT_toggle(bpy.types.Operator):
    """Mark this note resolved, or open again"""

    bl_idname = "view_notes.toggle"
    bl_label = "Toggle Resolved"
    note_id: IntProperty()

    def execute(self, context):
        def fn(n):
            n["status"] = "open" if n["status"] == "resolved" else "resolved"

        _edit_note(context, self.note_id, fn)
        return {"FINISHED"}


class VIEWNOTES_OT_delete(bpy.types.Operator):
    """Delete this note and its screenshot"""

    bl_idname = "view_notes.delete"
    bl_label = "Delete Note"
    note_id: IntProperty()

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        folder = notes_dir(context)

        def fn(n):
            p = os.path.join(folder, n.get("screenshot", ""))
            if n.get("screenshot") and os.path.exists(p):
                os.remove(p)
            return False

        _edit_note(context, self.note_id, fn)
        return {"FINISHED"}


class VIEWNOTES_OT_open_folder(bpy.types.Operator):
    """Open the notes folder"""

    bl_idname = "view_notes.open_folder"
    bl_label = "Open Notes Folder"

    def execute(self, context):
        folder = notes_dir(context)
        if folder is None:
            return {"CANCELLED"}
        os.makedirs(folder, exist_ok=True)
        bpy.ops.wm.path_open(filepath=folder)
        return {"FINISHED"}


# --- UI ------------------------------------------------------------------------------------------------------


class VIEWNOTES_PT_panel(bpy.types.Panel):
    bl_label = "View Notes"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Notes"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        folder = notes_dir(context)
        if folder is None:
            layout.label(text="Save the .blend to add notes", icon="INFO")
            return
        col = layout.column(align=True)
        col.prop(scene, "view_notes_text", text="")
        col.operator(VIEWNOTES_OT_draw.bl_idname, text="New Note (Ctrl+Alt+N)", icon="GREASEPENCIL")
        row = layout.row(align=True)
        row.prop(scene, "view_notes_show_resolved", text="Resolved", toggle=True)
        row.operator(VIEWNOTES_OT_open_folder.bl_idname, text="", icon="FILE_FOLDER")
        try:
            notes = load_notes(folder)["notes"]
        except (OSError, ValueError) as e:
            layout.label(text=f"notes.json unreadable: {e}", icon="ERROR")
            return
        shown = [n for n in notes if scene.view_notes_show_resolved or n["status"] != "resolved"]
        if len(shown) < len(notes):
            layout.label(text=f"{len(notes) - len(shown)} resolved hidden", icon="CHECKMARK")
        for n in reversed(shown):
            box = layout.box()
            row = box.row(align=True)
            done = n["status"] == "resolved"
            t = row.operator(VIEWNOTES_OT_toggle.bl_idname, text="", icon="CHECKBOX_HLT" if done else "CHECKBOX_DEHLT")
            t.note_id = n["id"]
            row.label(text=f"{n['id']}. {n['text'] or '(no text)'}")
            row.operator(VIEWNOTES_OT_goto.bl_idname, text="", icon="HIDE_OFF").note_id = n["id"]
            row.operator(VIEWNOTES_OT_delete.bl_idname, text="", icon="X").note_id = n["id"]
            if n.get("resolution"):
                box.label(text=n["resolution"], icon="INFO")


class VIEWNOTES_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    folder: StringProperty(
        name="Notes folder",
        description="Where notes are stored; // is the folder of the .blend",
        default="//view_notes",
    )
    author: StringProperty(name="Author", description="Stored with each note (optional)")
    hide_panels: BoolProperty(
        name="Hide panels, headers and navigation gizmo in screenshots",
        default=True,
    )
    clear_annotations: BoolProperty(
        name="Clear annotations after each note",
        description="Each note keeps the strokes drawn for it, and the viewport is clean for the next one",
        default=True,
    )

    def draw(self, context):
        for p in ("folder", "author", "hide_panels", "clear_annotations"):
            self.layout.prop(self, p)


classes = (
    VIEWNOTES_OT_add,
    VIEWNOTES_OT_draw,
    VIEWNOTES_OT_goto,
    VIEWNOTES_OT_toggle,
    VIEWNOTES_OT_delete,
    VIEWNOTES_OT_open_folder,
    VIEWNOTES_PT_panel,
    VIEWNOTES_Preferences,
)
_keymaps = []


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.view_notes_text = StringProperty(name="Note", description="Text of the next note")
    bpy.types.Scene.view_notes_show_resolved = BoolProperty(name="Show resolved notes", default=False)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
        kmi = km.keymap_items.new(VIEWNOTES_OT_draw.bl_idname, "N", "PRESS", ctrl=True, alt=True)
        _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    del bpy.types.Scene.view_notes_show_resolved
    del bpy.types.Scene.view_notes_text
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
