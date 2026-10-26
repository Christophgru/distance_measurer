"""Compact tracking diagnostics; no tracking or reconstruction logic."""

import cv2
import numpy as np


def draw_tracking_seeds(image, points, *, grid_rows, grid_cols, title="Tracking seeds"):
    canvas = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR) if image.ndim == 2 else image.copy()
    height, width = canvas.shape[:2]
    for x in np.minimum(width - 1, np.linspace(0, width, grid_cols + 1, dtype=int)):
        cv2.line(canvas, (x, 0), (x, height - 1), (180, 120, 30), 1)
    for y in np.minimum(height - 1, np.linspace(0, height, grid_rows + 1, dtype=int)):
        cv2.line(canvas, (0, y), (width - 1, y), (180, 120, 30), 1)
    for pixel in points:
        cv2.circle(canvas, tuple(np.rint(pixel).astype(int)), 3, (60, 240, 80), 1, cv2.LINE_AA)
    cv2.putText(
        canvas,
        f"{title}: {len(points)}",
        (12, 25),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return canvas


def draw_rotation_parallax(
    image, predicted, observed, in_bounds, selected, *, grid_rows, grid_cols
):
    canvas = draw_tracking_seeds(
        image, observed[selected], grid_rows=grid_rows, grid_cols=grid_cols, title="Ranked tracks"
    )
    height, width = canvas.shape[:2]
    visible = np.flatnonzero(np.isfinite(predicted).all(axis=1))
    if len(visible) > 150:
        visible = visible[np.linspace(0, len(visible) - 1, 150, dtype=int)]
    for index in visible:
        b = np.rint(observed[index]).astype(int)
        if in_bounds[index]:
            a = np.rint(predicted[index]).astype(int)
            cv2.line(canvas, tuple(a), tuple(b), (140, 140, 140), 1, cv2.LINE_AA)
            cv2.circle(canvas, tuple(a), 3, (255, 140, 20), 1, cv2.LINE_AA)
        else:
            # Keep a huge prediction's direction while bounding integer coordinates.
            direction = predicted[index] - observed[index]
            scale = max(abs(direction[0]) / width, abs(direction[1]) / height, 1)
            end = np.rint(observed[index] + 2 * direction / scale).astype(int)
            visible_line, first, last = cv2.clipLine((0, 0, width, height), tuple(b), tuple(end))
            if visible_line:
                cv2.arrowedLine(canvas, first, last, (255, 140, 20), 1, cv2.LINE_AA, tipLength=0.08)
        cv2.circle(canvas, tuple(b), 2, (255, 255, 255), 1, cv2.LINE_AA)
    for pixel in observed[selected]:
        cv2.circle(canvas, tuple(np.rint(pixel).astype(int)), 6, (20, 230, 255), 1, cv2.LINE_AA)
    cv2.putText(
        canvas,
        "Blue: rotation-only / white: observed / yellow: selected",
        (12, 50),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "Blue arrows point toward offscreen predictions",
        (12, 70),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    return canvas
