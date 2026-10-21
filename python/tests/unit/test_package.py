"""Check the installed package exposes the planned module structure."""

from importlib import import_module

import pytest


@pytest.mark.parametrize(
    "module_name",
    [
        "distance_measuring",
        "distance_measuring.dataset",
        "distance_measuring.camera",
        "distance_measuring.vision",
        "distance_measuring.optimization",
        "distance_measuring.imu",
        "distance_measuring.scene",
        "distance_measuring.visualization",
    ],
)
def test_package_is_importable(module_name):
    assert import_module(module_name).__name__ == module_name
