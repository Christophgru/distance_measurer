"""Calibration intake: native corners, reserved views and frozen-intrinsic checks."""

import math

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from distance_measuring.camera import CameraExtrinsics
from distance_measuring.camera.calibration import (
    _pose_from_homography,
    calibrate_planar_views,
    checkerboard_points,
    estimate_homography,
)


def detect_capture_boards(session, columns=9, rows=6, *, max_detection_size=1600):
    """Detect downscaled, then refine in unchanged stored/native JPEG pixels."""
    if columns < 3 or rows < 3 or type(max_detection_size) is not int or max_detection_size < 100:
        raise ValueError("Need >=3 inner corners per axis and detection size >=100")
    detections, observations = [], {}
    for event in session.images:
        gray = cv2.imread(
            str(session.image_path(event)), cv2.IMREAD_GRAYSCALE | cv2.IMREAD_IGNORE_ORIENTATION
        )
        if gray is None:
            raise ValueError(f"Cannot decode image {event.index}")
        height, width = gray.shape
        if (width, height) != (event.metadata.width, event.metadata.height):
            raise ValueError("Decoded calibration image size differs from capture metadata")
        ratio = min(1.0, max_detection_size / max(width, height))
        size = (max(1, round(width * ratio)), max(1, round(height * ratio)))
        small = cv2.resize(gray, size, interpolation=cv2.INTER_AREA) if ratio < 1 else gray
        found, corners = cv2.findChessboardCornersSB(
            small, (columns, rows), cv2.CALIB_CB_NORMALIZE_IMAGE
        )
        row = {
            "image_id": event.index,
            "found": bool(found),
            "native_size": [width, height],
            "detection_size": list(size),
        }
        if found:
            xy = (corners.reshape(-1, 2).astype(float) + 0.5) / (
                np.array(size) / [width, height]
            ) - 0.5
            xy = cv2.cornerSubPix(
                gray,
                xy.astype(np.float32).reshape(-1, 1, 2),
                (5, 5),
                (-1, -1),
                (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 40, 0.001),
            )
            xy = xy.reshape(-1, 2).astype(float)
            if not np.isfinite(xy).all() or np.any(xy < 0) or np.any(xy >= [width, height]):
                raise ValueError("Detected board corners lie outside the native image")
            observations[event.index] = xy
            row["native_pixels"] = xy.tolist()
            row["board_hull_fraction"] = float(
                cv2.contourArea(cv2.convexHull(xy.astype(np.float32))) / (width * height)
            )
        detections.append(row)
    return observations, detections


def _reserved_pose_check(board, pixels, intrinsics, columns):
    # Fit each held-out pose on alternating board corners. Other corners test
    # the pose and frozen K/distortion without participating in either fit.
    indices = np.arange(len(board))
    pose_mask = ((indices % columns + indices // columns) % 2) == 0
    test_mask = ~pose_mask
    ideal = intrinsics.undistort_points(pixels[pose_mask])
    homography, _ = estimate_homography(board[pose_mask, :2], ideal, solver="svd")
    initial = _pose_from_homography(homography, intrinsics.K)
    parameters = np.r_[Rotation.from_matrix(initial.R_cw).as_rotvec(), initial.O_cw]

    def decode(values):
        rotation = Rotation.from_rotvec(values[:3]).as_matrix()
        return CameraExtrinsics(rotation, -rotation.T @ values[3:])

    def residual(values):
        pose = decode(values)
        local = pose.world_to_camera(board[pose_mask])
        if np.any(local[:, 2] <= 1e-9):
            return np.full(int(pose_mask.sum()) * 2, 1e6)
        return (intrinsics.project(board[pose_mask], pose) - pixels[pose_mask]).ravel()

    fitted = least_squares(residual, parameters, max_nfev=100)
    pose = decode(fitted.x)
    if not fitted.success or np.any(pose.world_to_camera(board)[:, 2] <= 0):
        raise ValueError("Held-out board pose did not converge with positive depth")
    errors = np.linalg.norm(intrinsics.project(board[test_mask], pose) - pixels[test_mask], axis=1)
    return {
        "pose_fit_corner_indices": indices[pose_mask].tolist(),
        "tested_corner_indices": indices[test_mask].tolist(),
        "test_errors_native_px": errors.tolist(),
        "test_rmse_native_px": float(np.sqrt(np.mean(errors**2))),
        "test_p95_native_px": float(np.percentile(errors, 95)),
        "intrinsics_fitted_on_this_view": False,
    }


def audit_board_observations(
    observations, image_size, *, columns=9, rows=6, square_size_m=0.025, camera_id=None
):
    """Fit K on two thirds of detected views; reserve all remaining views.

    Return a report and a training-only calibration candidate, including when
    candidate validation fails. Never equate this with metric scene accuracy.
    """
    board = checkerboard_points(columns, rows, square_size_m)
    ids = sorted(observations)
    if any(type(i) is not int or i < 0 for i in ids):
        raise ValueError("Board image IDs must be nonnegative integers")
    for xy in observations.values():
        xy = np.asarray(xy, float)
        if xy.shape != (len(board), 2) or not np.isfinite(xy).all():
            raise ValueError("Each detected view needs all finite checkerboard corners")
        if np.any(xy < 0) or np.any(xy >= image_size):
            raise ValueError("Calibration corners lie outside the stored image")
    train = [i for n, i in enumerate(ids) if n % 3 != 2]
    reserved = [i for n, i in enumerate(ids) if n % 3 == 2]
    report = {
        "detected_views": len(ids),
        "training_image_ids": train,
        "reserved_image_ids": reserved,
        "split_rule": "Every third detected image in ID order is reserved before fitting",
        "held_out_views": [],
        "calibration_audit_passed": False,
        "metric_measurement_accepted": False,
        "blocking_reasons": [],
    }
    if len(ids) < 6:
        report["blocking_reasons"].append("need_at_least_six_detected_views")
        return report, None
    try:
        candidate = calibrate_planar_views(
            board,
            [observations[i] for i in train],
            image_size,
            solver="svd",
            camera_id=camera_id,
            view_names=tuple(str(i) for i in train),
        )
    except (ValueError, np.linalg.LinAlgError) as error:
        report["blocking_reasons"].append("training_calibration_failed")
        report["training_failure"] = str(error)
        return report, None
    candidate.diagnostics["intake_audit"] = {
        "training_image_ids": train,
        "reserved_image_ids": reserved,
        "profile_fitted_on_reserved_views": False,
    }
    report["training_rmse_native_px"] = candidate.rmse_px
    for i in reserved:
        try:
            row = _reserved_pose_check(
                board, np.asarray(observations[i]), candidate.intrinsics, columns
            )
            row.update(
                image_id=i,
                passed=row["test_rmse_native_px"] <= 2.0 and row["test_p95_native_px"] <= 3.0,
            )
        except (ValueError, np.linalg.LinAlgError) as error:
            row = {"image_id": i, "passed": False, "failure": str(error)}
        report["held_out_views"].append(row)
    if not candidate.diagnostics.get("optimization", {}).get("success", False):
        report["blocking_reasons"].append("training_solver_not_converged")
    if candidate.rmse_px > 2.0:
        report["blocking_reasons"].append("training_error_over_2_native_px")
    if not all(r["passed"] for r in report["held_out_views"]):
        report["blocking_reasons"].append("reserved_view_checks_failed")
    report["calibration_audit_passed"] = not report["blocking_reasons"]
    return report, candidate


def compare_capture_settings(calibration_session, measurement_session):
    """Compare recorded geometry settings; missing values remain unknown."""
    fields = (
        "width",
        "height",
        "camera_id",
        "physical_camera_id",
        "focal_length_mm",
        "zoom_ratio",
        "crop_region",
        "focus_distance_diopters",
    )

    def equal(a, b):
        if type(a) in (int, float) and type(b) in (int, float):
            return math.isclose(a, b, rel_tol=1e-4, abs_tol=1e-8)
        return a == b

    result = {}
    for field in fields:
        first = [getattr(e.metadata, field) for e in calibration_session.images]
        second = [getattr(e.metadata, field) for e in measurement_session.images]
        known = [v for v in first + second if v is not None]
        mismatch = any(not equal(v, known[0]) for v in known[1:])
        status = (
            "mismatch"
            if mismatch
            else (
                "matched"
                if first and second and all(v is not None for v in first + second)
                else "unknown"
            )
        )
        result[field] = {
            "status": status,
            "calibration_values": first,
            "measurement_values": second,
        }
    device_equal = calibration_session.metadata.device == measurement_session.metadata.device
    if not device_equal:
        status = "mismatch"
    elif any(v["status"] == "mismatch" for v in result.values()):
        status = "mismatch"
    elif all(v["status"] == "matched" for v in result.values()):
        status = "matched"
    else:
        status = "unknown"
    return {
        "status": status,
        "device_metadata_equal": device_equal,
        "fields": result,
        "recorded_settings_cannot_prove_identical_optics": True,
    }
