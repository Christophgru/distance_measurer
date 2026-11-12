"""Camera frames in a common world: lecture 002, slides/pages 39–45 and 81–83."""

from pathlib import Path

import numpy as np

from distance_measuring.camera import CameraExtrinsics


def plot_camera_frames(
    cameras: dict[int, CameraExtrinsics], output: Path, *, quality_note: str | None = None
) -> Path:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(10, 8))
    FigureCanvasAgg(figure)
    axes = figure.add_subplot(111, projection="3d")
    centers = np.array([camera.C_w for camera in cameras.values()])
    span = max(float(np.ptp(centers, axis=0).max()), 1.0)
    length = span * 0.2
    for index, camera in cameras.items():
        C = camera.C_w
        axes.scatter(*C, color="black", s=30)
        axes.text(*(C + length * 0.08), f"camera {index}")
        # Lecture 002 pp. 81–83: R_cw maps world to camera. Its transpose maps
        # camera basis vectors (X right, Y down, Z forward) into world vectors.
        for direction, color, name in zip(
            camera.R_cw, ("red", "green", "blue"), ("X", "Y", "Z"), strict=True
        ):
            axes.quiver(*C, *(length * direction), color=color, arrow_length_ratio=0.15)
            axes.text(*(C + length * direction), name, color=color, fontsize=8)
    middle = (centers.min(axis=0) + centers.max(axis=0)) / 2
    radius = span * 0.75
    axes.set(
        xlim=(middle[0] - radius, middle[0] + radius),
        ylim=(middle[1] - radius, middle[1] + radius),
        zlim=(middle[2] - radius, middle[2] + radius),
        xlabel="world X (arbitrary units)",
        ylabel="world Y (arbitrary units)",
        zlabel="world Z (arbitrary units)",
    )
    axes.set_box_aspect((1, 1, 1))
    axes.set_title(
        "Camera frames: independent unit-baseline chain\n"
        "Relative baseline scales unresolved; positions illustrative"
    )
    if quality_note:
        figure.text(0.5, 0.025, quality_note, ha="center", color="darkorange", fontsize=10)
    figure.tight_layout(rect=(0.04, 0.08, 0.96, 1))
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=140)
    return output
