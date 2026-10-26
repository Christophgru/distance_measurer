"""Match diagnostics following lecture 002, slides/pages 138–140."""

import cv2
import numpy as np


def draw_match_diagnostic(
    first: np.ndarray,
    second: np.ndarray,
    points_first: np.ndarray,
    points_second: np.ndarray,
    inliers: np.ndarray,
    *,
    title: str,
    rectified: bool = False,
    panel_width: int = 800,
    max_lines: int = 60,
    accepted_label: str = "ACCEPTED by epipolar geometry",
    rejected_label: str = "REJECTED by epipolar geometry",
) -> np.ndarray:
    """Separate accepted/rejected rows; sample only the drawing, never the data."""
    panels, scales = [], []
    for image in (first, second):
        scale = min(1.0, panel_width / image.shape[1])
        panels.append(
            cv2.resize(image, (round(image.shape[1] * scale), round(image.shape[0] * scale)))
        )
        scales.append(
            np.array([panels[-1].shape[1] / image.shape[1], panels[-1].shape[0] / image.shape[0]])
        )
    width = sum(panel.shape[1] for panel in panels)
    height = max(panel.shape[0] for panel in panels)
    header = 76
    canvas = np.full((2 * (height + header), width, 3), 24, dtype=np.uint8)
    points1, points2 = points_first * scales[0], points_second * scales[1]
    visible = (
        np.isfinite(points1).all(axis=1)
        & np.isfinite(points2).all(axis=1)
        & (points1 >= 0).all(axis=1)
        & (points2 >= 0).all(axis=1)
        & (points1 < np.array([panels[0].shape[1], panels[0].shape[0]])).all(axis=1)
        & (points2 < np.array([panels[1].shape[1], panels[1].shape[0]])).all(axis=1)
    )
    for row, (mask, label, color) in enumerate(
        (
            (inliers, accepted_label, (70, 235, 70)),
            (~inliers, rejected_label, (70, 70, 255)),
        )
    ):
        top = row * (height + header)
        for panel, left in ((panels[0], 0), (panels[1], panels[0].shape[1])):
            canvas[top + header : top + header + panel.shape[0], left : left + panel.shape[1]] = (
                panel
            )
        # Lecture 002 p. 139: corresponding points should share horizontal scanlines.
        if rectified:
            for y in range(top + header + 40, top + header + height, 60):
                cv2.line(canvas, (0, y), (width - 1, y), (180, 130, 45), 1)
        indices = np.flatnonzero(mask & visible)
        if len(indices) > max_lines:
            indices = indices[np.linspace(0, len(indices) - 1, max_lines, dtype=int)]
        for index in indices:
            a = np.rint(points1[index]).astype(int) + [0, top + header]
            b = np.rint(points2[index]).astype(int) + [panels[0].shape[1], top + header]
            cv2.line(canvas, tuple(a), tuple(b), color, 1, cv2.LINE_AA)
            cv2.circle(canvas, tuple(a), 3, color, 1, cv2.LINE_AA)
            cv2.circle(canvas, tuple(b), 3, color, 1, cv2.LINE_AA)
        for offset, text in (
            (25, title),
            (55, f"{label}: {mask.sum()} total; {len(indices)} visible matches drawn"),
        ):
            cv2.putText(
                canvas,
                text,
                (12, top + offset),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                1,
                cv2.LINE_AA,
            )
    return canvas
