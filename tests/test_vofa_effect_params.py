"""Self-owned display filter parameters; no native shader equivalence claim."""

from __future__ import annotations

import json

import pytest
from PySide6.QtGui import QColor, QImage

from qx_stc_isp.vofa.image_effects import apply_image_effect, effect_parameter
from qx_stc_isp.vofa.images import ImageViewWidget
from qx_stc_isp.vofa.workspace import WorkspaceSettings, load_workspace, save_workspace


def gradient(width: int = 64, height: int = 64) -> QImage:
    image = QImage(width, height, QImage.Format_RGB32)
    for y in range(height):
        for x in range(width):
            image.setPixelColor(x, y, QColor(x * 4 % 256, y * 4 % 256, (x + y) % 256))
    return image


def test_parameter_changes_effect_output():
    image = gradient()
    dark = apply_image_effect(image, "threshold", 0).image
    bright = apply_image_effect(image, "threshold", 255).image
    assert dark.pixelColor(0, 0).red() == 255
    assert bright.pixelColor(0, 0).red() == 0
    small = apply_image_effect(image, "pixelate", 2).image
    large = apply_image_effect(image, "pixelate", 32).image
    assert small != large
    assert small.size() == image.size() == large.size()


def test_zero_strength_returns_source_and_invalid_parameters_raise():
    image = gradient()
    for effect in ("grayscale", "invert", "blur", "sharpen", "edges", "emboss", "sepia"):
        result = apply_image_effect(image, effect, 0)
        assert not result.reduced and result.image.size() == image.size()
        assert result.image == image
    with pytest.raises(ValueError):
        apply_image_effect(image, "threshold", 300)
    with pytest.raises(ValueError):
        apply_image_effect(image, "pixelate", 1)
    with pytest.raises(ValueError):
        apply_image_effect(image, "blur", True)


def test_image_view_exposes_parameter_range_and_resets_default(qtbot):
    view = ImageViewWidget()
    qtbot.addWidget(view)
    view.set_effect("threshold")
    assert view.effect_param_spin.minimum() == 0 and view.effect_param_spin.maximum() == 255
    assert view.effect_parameter == 128
    view.set_effect_parameter(42)
    assert view.effect_parameter == 42
    view.set_effect("pixelate")
    assert view.effect_parameter == effect_parameter("pixelate").default
    assert view.effect_param_spin.minimum() == 2 and view.effect_param_spin.maximum() == 64
    with pytest.raises(Exception):
        view.set_effect_parameter(1)


def test_native_style_effects_are_bounded_and_parameterized():
    image = gradient()
    ramp = gradient()
    for y in range(64):
        for x in range(64):
            ramp.setPixelColor(x, y, QColor((x % 8) * 32, 0, 0))
    for effect in ("posterize", "toon", "glow", "vignette", "billboard", "warhol",
                   "tilt_shift", "isolate", "shockwave", "wobble"):
        sample = ramp if effect == "toon" else image
        spec = effect_parameter(effect)
        low = apply_image_effect(sample, effect, spec.minimum)
        high = apply_image_effect(sample, effect, spec.maximum)
        assert low.image.size() == high.image.size() == sample.size()
        assert not low.image.isNull() and not high.image.isNull()
        assert low.image != high.image, effect
    with pytest.raises(ValueError):
        apply_image_effect(image, "isolate", 256)


def test_workspace_roundtrips_effect_parameter_and_upgrades_missing_field(tmp_path):
    settings = WorkspaceSettings(image_effect="blur", image_effect_param=40)
    path = tmp_path / "workspace.json"
    save_workspace(path, settings)
    assert load_workspace(path).image_effect_param == 40
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("image_effect_param")
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    assert load_workspace(path).image_effect_param == effect_parameter("blur").default
    with pytest.raises(ValueError):
        WorkspaceSettings(image_effect_param=300)
