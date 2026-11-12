"""Projection windows and sparse epipolar corridors for P5 diagnostics."""

import cv2
import numpy as np


def draw_projections(image, rows, searches, *, title):
    scale = min(1.0, 1000 / image.shape[1])
    canvas = cv2.resize(image, None, fx=scale, fy=scale)
    matches = {r["point_id"]: r for r in searches}
    for row in rows[:150]:
        if not row["visible"]:
            continue
        pixel = tuple(np.rint(np.asarray(row["predicted_pixel"]) * scale).astype(int))
        result = matches.get(row["point_id"])
        if result is None:
            # Already verified temporal inliers need no patch search/window.
            cv2.circle(canvas, pixel, 3, (255, 160, 30), -1)
            continue
        color = (60, 220, 60) if result["accepted"] else (60, 60, 230)
        cv2.circle(
            canvas, pixel, max(1, round(row["search_radius_px"] * scale)), color, 1, cv2.LINE_AA
        )
        cv2.circle(canvas, pixel, 2, (255, 160, 30), -1)
        if result["accepted"]:
            observed = tuple(
                np.rint(np.asarray(result["second_pixel_refined"]) * scale).astype(int)
            )
            cv2.line(canvas, pixel, observed, color, 1, cv2.LINE_AA)
            cv2.circle(canvas, observed, 3, color, 1)
    cv2.putText(canvas, title, (12, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
    cv2.putText(
        canvas,
        "Blue: prediction; green/red: accepted/rejected patch window",
        (12, 50),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (255, 255, 255),
        1,
    )
    return canvas


def draw_corridors(image, report, corridor_px):
    scale = min(1.0, 700 / image.shape[1])
    base = cv2.resize(image, None, fx=scale, fy=scale)
    height, width = base.shape[:2]
    panels = []
    queries = report.get("queries", [])
    # Include successes and failures where both are available.
    chosen = [
        next((r for r in queries if r["accepted"] is status), None) for status in (True, False)
    ]
    chosen = [r for r in chosen if r is not None]
    chosen += [r for r in queries if r not in chosen][: 4 - len(chosen)]
    for row in chosen or [None]:
        panel = base.copy()
        if row:
            a, b, c = row["line"]
            candidates = []
            if abs(b) > 1e-12:
                candidates += [
                    (0, -c / b),
                    ((width - 1) / scale, -(a * (width - 1) / scale + c) / b),
                ]
            if abs(a) > 1e-12:
                candidates += [
                    (-c / a, 0),
                    (-(b * (height - 1) / scale + c) / a, (height - 1) / scale),
                ]
            endpoints = [
                np.rint(np.asarray(p) * scale).astype(int)
                for p in candidates
                if -1 <= p[0] * scale <= width and -1 <= p[1] * scale <= height
            ]
            if len(endpoints) >= 2:
                overlay = panel.copy()
                cv2.line(
                    overlay,
                    tuple(endpoints[0]),
                    tuple(endpoints[-1]),
                    (255, 140, 20),
                    max(2, round(2 * corridor_px * scale)),
                    cv2.LINE_AA,
                )
                panel = cv2.addWeighted(overlay, 0.5, panel, 0.5, 0)
            for point in row["candidate_pixels"]:
                cv2.circle(
                    panel,
                    tuple(np.rint(np.asarray(point) * scale).astype(int)),
                    3,
                    (60, 60, 240),
                    1,
                )
            if row.get("target_pixel"):
                color = (50, 240, 50) if row["accepted"] else (60, 60, 240)
                cv2.circle(
                    panel,
                    tuple(np.rint(np.asarray(row["target_pixel"]) * scale).astype(int)),
                    6,
                    color,
                    2,
                )
            label = (
                f"Feature {row['source_index']}: {row['candidate_count']} candidates; "
                f"accepted={row['accepted']}"
            )
        else:
            label = report.get("reason") or "No non-landmark queries"
        cv2.putText(panel, label, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 2)
        cv2.putText(
            panel,
            "Blue corridor; green match; red candidate/rejection",
            (10, 48),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.44,
            (255, 255, 255),
            1,
        )
        panels.append(panel)
    while len(panels) < 4:
        panels.append(np.zeros_like(base))
    return np.vstack([np.hstack(panels[:2]), np.hstack(panels[2:4])])
