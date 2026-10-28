"""Bounded local STL/OBJ wireframe loading for the attitude control."""

from __future__ import annotations

import struct

import pytest

from qx_stc_isp.vofa.controls import ControlSpec
from qx_stc_isp.vofa.extra_controls import CubeView, load_wireframe_model

STL = """solid t
facet normal 0 0 1
 outer loop
  vertex 0 0 0
  vertex 2 0 0
  vertex 0 2 0
 endloop
endfacet
endsolid t
"""

OBJ = """v 0 0 0
v 1 0 0
v 1 1 0
v 0 1 0
f 1 2 3 4
"""


def test_ascii_stl_and_obj_are_normalized_and_deduplicated(tmp_path):
    stl = tmp_path / "triangle.stl"
    stl.write_text(STL, encoding="utf-8")
    vertices, edges = load_wireframe_model(str(stl))
    assert len(vertices) == 3 and len(edges) == 3
    assert all(-1 <= value <= 1 for point in vertices for value in point)

    obj = tmp_path / "quad.obj"
    obj.write_text(OBJ, encoding="utf-8")
    vertices, edges = load_wireframe_model(str(obj))
    assert len(vertices) == 4 and len(edges) == 5


def test_binary_stl_is_loaded(tmp_path):
    binary = tmp_path / "tri.stl"
    triangle = (0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 2.0, 0.0, 0.0, 0.0, 2.0, 0.0)
    binary.write_bytes(b"\0" * 80 + struct.pack("<I", 1) + struct.pack("<12fH", *triangle, 0))
    vertices, edges = load_wireframe_model(str(binary))
    assert len(vertices) == 3 and len(edges) == 3


def test_invalid_or_unknown_model_falls_back_without_reading(tmp_path):
    assert load_wireframe_model("") is None
    assert load_wireframe_model(str(tmp_path / "missing.stl")) is None
    text = tmp_path / "model.txt"
    text.write_text(OBJ, encoding="utf-8")
    assert load_wireframe_model(str(text)) is None
    bad = tmp_path / "bad.obj"
    bad.write_text("v 0 0 0\nf 1 2 3\n", encoding="utf-8")
    assert load_wireframe_model(str(bad)) is None


def test_cube_accepts_model_path_and_renders(qtbot, tmp_path):
    stl = tmp_path / "triangle.stl"
    stl.write_text(STL, encoding="utf-8")
    assert ControlSpec("cube", 0, "姿态", "", {"model_path": str(stl)}).settings["model_path"] == str(stl)
    with pytest.raises(ValueError):
        ControlSpec("cube", 0, "姿态", "", {"model_path": "x" * 1025})

    view = CubeView(model_path=str(stl))
    qtbot.addWidget(view)
    view.resize(200, 200)
    view.set_values(10, 20, 30)
    assert view._model_loaded and not view.grab().isNull()
    fallback = CubeView(model_path=str(tmp_path / "missing.stl"))
    qtbot.addWidget(fallback)
    assert not fallback._model_loaded
