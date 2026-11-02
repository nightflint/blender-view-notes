# View Notes

A Blender extension for leaving review notes pinned to a viewport view. Press **Ctrl+Alt+N** in the 3D
Viewport, type what is wrong, and the note is saved with everything needed to look at the same thing
again:

- the exact view: view and projection matrices, eye position, lens, field of view, region size;
- the shading mode (Wireframe, Solid, Material Preview, Rendered) and the wireframe overlay;
- the selection: selected objects, or in Edit Mode the selected vertices and faces with their bounds;
- annotation strokes drawn for the note, in world space (cleared afterwards, so each note keeps its own);
- a screenshot of the viewport, without the panels drawn over it.

It was written so a developer can hand feedback to AI agents (or teammates) that work on the model
headless: instead of taking screenshots, switching windows and describing where to look, the reviewer
leaves a note, and the agent re-renders that exact view on its next version of the model.

## Install

Blender 5.1 or later. Download the zip from the releases page (or build it, below), then
*Edit > Preferences > Get Extensions > Install from Disk*, or:

```
blender --command extension install-file -r user_default -e view_notes-0.2.0.zip
```

## Use

- **Ctrl+Alt+N** in the 3D Viewport (or *New Note* in the sidebar's *Notes* tab) switches to the
  Annotate tool: draw on the model to point at things (strokes stick to the surface, Ctrl+Z undoes
  one, you can still orbit and zoom), then press **Enter** to write the note, or **Esc** to cancel.
  Your previous tool comes back afterwards. Text typed in the *Notes* tab's field beforehand is used
  as the note without a dialog.
- The *Notes* tab lists the notes for this folder: go back to a note's view (eye icon), mark it
  resolved (checkbox), delete it (X), open the notes folder.
- The .blend must be saved: notes are stored next to it, in `//view_notes/` by default
  (*Preferences > Add-ons > View Notes*).

## File format

`view_notes/notes.json`, plus one `note_NNN.png` screenshot per note:

```jsonc
{
  "format": 1,
  "notes": [{
    "id": 1,
    "created": "2026-11-02T14:03:11",
    "author": "",
    "blend": "model.blend",
    "text": "the left ear is too thin from here",
    "status": "open",              // open | resolved; tools may set this and "resolution"
    "resolution": "",
    "screenshot": "note_001.png",  // the viewport region, the same pixels the projection covers
    "frame": 1,
    "view": {
      "perspective": "PERSP",      // PERSP | ORTHO | CAMERA
      "is_perspective": true,
      "view_matrix": [[...]],      // 4x4 world to view, rows; the view looks down -Z
      "window_matrix": [[...]],    // 4x4 view to clip (OpenGL convention), rows
      "eye": [x, y, z],
      "view_location": [x, y, z],  // the orbit centre
      "view_rotation": [w, x, y, z],
      "view_distance": 1.2,
      "lens": 50.0,
      "clip_start": 0.01, "clip_end": 1000.0,
      "region_size": [1311, 868],  // the screenshot's size
      "fov_x_deg": 71.5, "fov_y_deg": 50.2   // perspective; ORTHO gives ortho_width, ortho_height
    },
    "shading": {"type": "MATERIAL", "wireframe_overlay": true, "xray": false, "light": "STUDIO",
                "color_type": "MATERIAL", "backface_culling": false, "studio_light": "forest.exr",
                "use_scene_world": false},
    "selection": {
      "mode": "EDIT_MESH",
      "active_object": "body",
      "selected_objects": ["body"],
      "edit_selection": [{"object": "body", "vertex_count": 50, "face_count": 12,
                          "vertices": [...], "faces": [...],
                          "center": [x, y, z], "min": [x, y, z], "max": [x, y, z]}]
      // in Object Mode: "selected_bounds": {"center", "min", "max"}
    },
    "cursor": [x, y, z],
    "annotations": [{"layer": "Note", "color": [r, g, b], "points": [[x, y, z], ...]}]
  }]
}
```

To render a note's view with a Blender camera: the camera's world matrix is `view_matrix` inverted;
for a perspective view set the horizontal field of view to `fov_x_deg` (sensor fit horizontal) and the
resolution to `region_size`. If `window_matrix[0][2]` or `[1][2]` is not zero the view is off-centre:
shift the camera by half of it, in units of the frame.

Vertex and face indices refer to the mesh as it was when the note was taken; the bounds stay useful
after the mesh changes.

## Build

```
blender --command extension build --source-dir . --output-dir dist
```

## License

GPL-3.0-or-later, as required for Blender add-ons.
