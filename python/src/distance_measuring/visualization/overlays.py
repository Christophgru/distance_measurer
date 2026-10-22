"""Red/blue diagnostics; lecture 002 pp. 81–83, 138–140 (PPTX slide order)."""

import cv2
import numpy as np

from distance_measuring.scene import SceneCamera


def red_blue_overlay(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """RGB output: first grayscale in red, second in blue, coincidences magenta."""
    if first.shape != second.shape or first.ndim != 2 or first.dtype != second.dtype:
        raise ValueError("Overlay inputs must be equal-shaped grayscale arrays of the same dtype")
    if first.dtype != np.uint8:
        raise ValueError("Overlay inputs must be uint8")
    return np.stack((first, np.zeros_like(first), second), axis=-1)


def pose_rectification(
    first: SceneCamera, second: SceneCamera
) -> tuple[np.ndarray, np.ndarray, str]:
    """Ideal-pixel homographies and disparity direction from the supplied poses.

    This does not fit or update camera parameters or use image correspondences.
    """
    if first.intrinsics.image_size != second.intrinsics.image_size:
        raise ValueError("Rectification requires equal working image sizes")
    a, b = first.extrinsics, second.extrinsics
    # Lecture 002 pp. 81–83: Pc=R(Pw-C). Eliminate Pw to express camera 1
    # coordinates in camera 2: R21=R2 R1.T, t21=R2(C1-C2), NOT C2-C1.
    rotation = b.R_cw @ a.R_cw.T
    translation = b.R_cw @ (a.C_w - b.C_w)
    if np.linalg.norm(translation) < 1e-12:
        raise ValueError("Rectification requires distinct camera centers")
    K1, K2 = first.intrinsics.K, second.intrinsics.K
    # pp. 138–140 motivate common epipolar scanlines. OpenCV's calibrated
    # stereoRectify is our implementation choice; it may choose vertical stereo.
    R1, R2, _, P2, *_ = cv2.stereoRectify(
        K1,
        None,
        K2,
        None,
        first.intrinsics.image_size,
        rotation,
        translation.reshape(3, 1),
        flags=cv2.CALIB_ZERO_DISPARITY,
        alpha=-1,
    )
    # pp. 70–76, 133–134: K^-1 m is a ray. Rotate that ray, then project
    # into the virtual camera. H is an image mapping, not a scene 3x4 M.
    # Lecture 002 p. 134's virtual camera allows a chosen K. Keep its focal
    # length positive: automatic all-pixel scaling can become negative when
    # a strongly oblique view crosses the rectified camera's image horizon.
    width, height = first.intrinsics.image_size
    focal = (K1[0, 0] + K1[1, 1] + K2[0, 0] + K2[1, 1]) / 4
    virtual_K = np.array([[focal, 0, (width - 1) / 2], [0, focal, (height - 1) / 2], [0, 0, 1]])
    H1 = virtual_K @ R1 @ np.linalg.inv(K1)
    H2 = virtual_K @ R2 @ np.linalg.inv(K2)
    # One COMMON viewport preserves epipolar scanlines. Sample image support,
    # excluding rays near/behind the virtual horizon, and trim extreme 2% tails.
    # This display crop fits no correspondences and changes neither K nor poses
    # in the source scene. It cannot turn inconsistent scanlines into agreement.
    x, y = np.meshgrid(np.linspace(0, width - 1, 41), np.linspace(0, height - 1, 31))
    samples = np.c_[x.ravel(), y.ravel(), np.ones(x.size)]
    supports = []
    for H in (H1, H2):
        projected = samples @ H.T
        projected = projected[projected[:, 2] > 0.2]
        if not len(projected):
            raise ValueError("No forward image support in the rectified camera")
        supports.append(projected[:, :2] / projected[:, 2:])
    lo, hi = np.percentile(np.vstack(supports), [2, 98], axis=0)
    scale = min(0.94 * width / (hi[0] - lo[0]), 0.94 * height / (hi[1] - lo[1]))
    offset = np.array([width, height]) / 2 - scale * (hi + lo) / 2
    viewport = np.array([[scale, 0, offset[0]], [0, scale, offset[1]], [0, 0, 1]])
    direction = "horizontal" if abs(P2[0, 3]) > abs(P2[1, 3]) else "vertical"
    return viewport @ H1, viewport @ H2, direction
