"""Plot measurement01 red/blue overlaps and its estimated phone motion.

Defaults use the existing M4/M5/M7 artifacts. Outputs are diagnostics only;
they do not alter calibration, poses, reconstruction or the failed M9 gate.
"""

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import matplotlib
import numpy as np

from distance_measuring.camera import CameraIntrinsics
from distance_measuring.scene import Scene
from distance_measuring.visualization.overlays import pose_rectification, red_blue_overlay


def artifact(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError(f"Artifact must remain inside {root}: {name}")
    return path


def upright(image, enabled):
    return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE) if enabled else image


def overlays(images, report, cameras, matches_root, output, rotate, plt):
    figure, axes = plt.subplots(3, 3, figsize=(15, 17), facecolor="#151922")
    records = []
    for row, pair in enumerate(report["pairs"]):
        i, j = pair["indices"]
        size = cameras[i].intrinsics.image_size
        with np.load(artifact(matches_root, pair["matches_file"]), allow_pickle=False) as data:
            H1, H2, direction = pose_rectification(cameras[i], cameras[j])
            modes = [
                ("raw", np.eye(3), np.eye(3), None),
                ("m4_matches", data["H_first"], data["H_second"], "horizontal"),
                ("m7_poses", H1, H2, direction),
            ]
        for column, (mode, first_H, second_H, disparity) in enumerate(modes):
            red, blue = [
                upright(cv2.warpPerspective(images[k], H, size), rotate)
                for k, H in ((i, first_H), (j, second_H))
            ]
            rgb = red_blue_overlay(red, blue)
            stem = f"pair_{i:03d}_{j:03d}_{mode}"
            # Save RGB through OpenCV's BGR writer without swapping the legend.
            if not cv2.imwrite(str(output / f"{stem}.png"), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)):
                raise OSError(f"Could not save overlay {stem}")
            ax = axes[row, column]
            ax.imshow(rgb)
            if rotate and disparity:
                disparity = "vertical" if disparity == "horizontal" else "horizontal"
            if disparity == "horizontal":
                for y in range(0, rgb.shape[0], 100):
                    ax.axhline(y, color="#91edb7", alpha=0.35, lw=0.5)
            elif disparity == "vertical":
                for x in range(0, rgb.shape[1], 100):
                    ax.axvline(x, color="#91edb7", alpha=0.35, lw=0.5)
            title = {
                "raw": "Original pixel overlap",
                "m4_matches": "M4 image-match rectification",
                "m7_poses": "M7 estimated-pose rectification",
            }[mode]
            ax.set_title(f"{i} RED + {j} BLUE | {title}", color="white", fontsize=10)
            ax.set_xlabel(
                "No alignment applied" if not disparity else f"Expected disparity: {disparity}",
                color="#c5cedd",
                fontsize=9,
            )
            ax.set_xticks([])
            ax.set_yticks([])
            records.append(
                {
                    "pair": [i, j],
                    "mode": mode,
                    "file": f"{stem}.png",
                    "disparity_direction_display": disparity,
                    "H_first": first_H.tolist(),
                    "H_second": second_H.tolist(),
                }
            )
    figure.suptitle(
        "Red / blue image overlaps — measurement01\n"
        "Magenta = coincident intensity. Along-scanline separation can be normal depth disparity.\n"
        "Corresponding features should agree across scanlines; black borders are warp coverage.",
        color="white",
        fontsize=13,
    )
    figure.tight_layout(rect=(0, 0, 1, 0.94))
    figure.savefig(output / "overlays_overview.png", dpi=170, facecolor=figure.get_facecolor())
    return records


def motion(scene, initial, report, output, plt):
    by_id = {c.image_id: c for c in scene.cameras}
    records = sorted(report["images"], key=lambda image: image["timestamp_ns"])
    ids = [image["index"] for image in records]
    cameras = [by_id[i] for i in ids]
    centers = np.array([c.extrinsics.C_w for c in cameras])
    old = {c["index"]: np.array(c["C_w"]) for c in initial["cameras"]}
    old_centers = np.array([old[i] for i in ids])
    seconds = np.array([(r["timestamp_ns"] - records[0]["timestamp_ns"]) / 1e9 for r in records])
    colors = ["#e74c3c", "#3498db", "#27ae60"]
    span = max(float(np.ptp(np.vstack((centers, old_centers)), axis=0).max()), 0.1)
    length = 0.18 * span
    figure = plt.figure(figsize=(18, 10), layout="constrained")
    grid = figure.add_gridspec(2, 3)
    space = figure.add_subplot(grid[:, 0], projection="3d")
    space.plot(*old_centers.T, "--", color=".65", label="M5 initial chain")
    space.plot(*centers.T, "-", color="#22364d", label="M7 bundle-adjusted")
    for i, camera, color in zip(ids, cameras, colors, strict=True):
        pose = camera.extrinsics
        space.scatter(*pose.C_w, color=color, s=55)
        space.text(*(pose.C_w + 0.035 * span), f"phone {i}")
        # Lecture 002 pp. 81–83: world basis directions of the camera are rows
        # of R_cw, equivalently columns of R_cw.T. +Z is its viewing direction.
        for basis, color_axis in zip(pose.R_cw, ["#e74c3c", "#27ae60", "#3498db"], strict=True):
            space.quiver(*pose.C_w, *(length * basis), color=color_axis, arrow_length_ratio=0.2)
    for a, b in zip(centers[:-1], centers[1:], strict=True):
        space.quiver(*a, *(b - a), color="#22364d", arrow_length_ratio=0.08)
    mid = (np.vstack((centers, old_centers)).min(0) + np.vstack((centers, old_centers)).max(0)) / 2
    for setter, value in zip([space.set_xlim, space.set_ylim, space.set_zlim], mid, strict=True):
        setter(value - 0.75 * span, value + 0.75 * span)
    space.set_box_aspect((1, 1, 1))
    space.view_init(elev=24, azim=-58)
    space.set(
        xlabel="world X (reconstruction units)",
        ylabel="world Y (reconstruction units)",
        zlabel="world Z (reconstruction units)",
        title="Path and phone orientations\nAxes: X red, Y green, forward Z blue",
    )
    space.legend(loc="upper left", fontsize=8)
    for index, (a, b) in enumerate([(0, 1), (0, 2)]):
        ax = figure.add_subplot(grid[0, index + 1])
        ax.plot(old_centers[:, a], old_centers[:, b], "--", color=".65", label="M5 initial")
        ax.plot(centers[:, a], centers[:, b], "-", color="#22364d", label="M7 refined")
        for i, camera, color in zip(ids, cameras, colors, strict=True):
            C = camera.extrinsics.C_w
            forward = camera.extrinsics.R_cw[2]
            ax.scatter(C[a], C[b], color=color)
            ax.annotate(str(i), (C[a], C[b]), xytext=(6, 6), textcoords="offset points")
            ax.quiver(
                C[a],
                C[b],
                length * forward[a],
                length * forward[b],
                angles="xy",
                scale_units="xy",
                scale=1,
                color=color,
            )
        ax.set(
            xlabel=f"world {'XYZ'[a]} (reconstruction units)",
            ylabel=f"world {'XYZ'[b]} (reconstruction units)",
            title="Projected path; arrows = viewing direction",
        )
        ax.set_aspect("equal", adjustable="datalim")
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8)
    position_ax = figure.add_subplot(grid[1, 1])
    for component, color in zip(range(3), ["#e74c3c", "#27ae60", "#3498db"], strict=True):
        position_ax.plot(seconds, centers[:, component], "o-", color=color, label="XYZ"[component])
    position_ax.set(
        xlabel="Seconds after first photo",
        ylabel="Position (reconstruction units)",
        title="Estimated camera centers at capture times",
    )
    position_ax.grid(alpha=0.25)
    position_ax.legend()
    rotation_ax = figure.add_subplot(grid[1, 2])
    # Lecture 002 pp. 36–39, 81: compare orientations in a common world.
    # Trace-to-angle conversion is our diagnostic extension, not Euler angles.
    angles = [
        float(
            np.degrees(
                np.arccos(
                    np.clip(
                        (np.trace(c.extrinsics.R_cw @ cameras[0].extrinsics.R_cw.T) - 1) / 2, -1, 1
                    )
                )
            )
        )
        for c in cameras
    ]
    rotation_ax.plot(seconds, angles, "o-", color="#8456a3")
    rotation_ax.set(
        xlabel="Seconds after first photo",
        ylabel="Rotation from photo 0 (degrees)",
        title="Estimated orientation change",
    )
    rotation_ax.grid(alpha=0.25)
    figure.suptitle(
        "Estimated phone motion — measurement01\n"
        "Arbitrary scale; accuracy unverified. Lines connect photo poses, not an IMU path.",
        fontsize=14,
    )
    figure.savefig(output / "phone_estimated_motion.png", dpi=170)
    figure.savefig(output / "phone_estimated_motion.svg")
    return [
        {
            "image_id": i,
            "elapsed_s": float(t),
            "C_w": c.extrinsics.C_w.tolist(),
            "R_cw": c.extrinsics.R_cw.tolist(),
            "rotation_from_first_degrees": angle,
        }
        for i, t, c, angle in zip(ids, seconds, cameras, angles, strict=True)
    ]


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, default=root / "data/raw/measurement01")
    parser.add_argument("--matches", type=Path, default=root / "data/processed/measurement01/m4")
    parser.add_argument(
        "--scene", type=Path, default=root / "data/processed/measurement01/m7/scene.json"
    )
    parser.add_argument(
        "--poses", type=Path, default=root / "data/processed/measurement01/m5/poses.json"
    )
    parser.add_argument(
        "--output", type=Path, default=root / "data/processed/measurement01/visual_diagnostics"
    )
    parser.add_argument(
        "--stored-orientation",
        action="store_true",
        help="Disable the measurement01 90-degree clockwise display rotation",
    )
    parser.add_argument(
        "--show", action="store_true", help="Open Matplotlib windows for zoom/pan on a desktop"
    )
    args = parser.parse_args()
    if not args.show:
        matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    output = args.output.resolve()
    session = args.session.resolve()
    matches = args.matches.resolve()
    protected = [
        session,
        matches,
        args.scene.resolve().parent,
        args.poses.resolve().parent,
        root / "data/raw",
    ]
    if any(output.is_relative_to(path) for path in protected):
        parser.error("Output must be outside raw and source artifact directories")
    sources = [args.scene.resolve(), args.poses.resolve(), matches / "matches.json"]
    content = args.scene.read_bytes()
    from distance_measuring.scene.storage import decode_document

    scene = Scene.from_dict(decode_document(content, args.scene.suffix))
    report = json.loads((matches / "matches.json").read_text())
    initial = json.loads(args.poses.read_text())
    if (
        scene.metadata.get("session_id") != report["session_id"]
        or initial["session_id"] != report["session_id"]
    ):
        parser.error("Scene, poses and matches must describe the same recording")
    if json.loads((session / "session.json").read_text())["session_id"] != report["session_id"]:
        parser.error("Raw session does not match reconstruction")
    cameras = {c.image_id: c for c in scene.cameras}
    images = {}
    for record in report["images"]:
        i = record["index"]
        path = artifact(session, record["filename"])
        sources.append(path)
        data = record["working_intrinsics"]
        K = CameraIntrinsics.from_K(
            data["camera_matrix"],
            (data["image_width"], data["image_height"]),
            distortion=record["stored_intrinsics"]["distortion"],
        )
        if not np.allclose(K.K, cameras[i].intrinsics.K):
            parser.error("M4 and scene intrinsic matrices differ")
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE | cv2.IMREAD_IGNORE_ORIENTATION)
        if image is None:
            parser.error(f"Cannot read {path}")
        image = cv2.resize(image, K.image_size, interpolation=cv2.INTER_AREA)
        images[i] = K.undistort_image(image) if any(K.distortion) else image
    for pair in report["pairs"]:
        sources.append(artifact(matches, pair["matches_file"]))
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    output.mkdir(parents=True, exist_ok=True)
    records = overlays(images, report, cameras, matches, output, not args.stored_orientation, plt)
    poses = motion(scene, initial, report, output, plt)
    if any(
        hashlib.sha256(Path(path).read_bytes()).hexdigest() != value
        for path, value in hashes.items()
    ):
        raise RuntimeError("An input changed during visualization")
    (output / "diagnostics.json").write_text(
        json.dumps(
            {
                "input_sha256": hashes,
                "display_rotation_clockwise_degrees": 0 if args.stored_orientation else 90,
                "overlay_rgb": {
                    "red": "first image grayscale",
                    "blue": "second image grayscale",
                    "green": "zero",
                },
                "overlays": records,
                "motion": poses,
                "units": "reconstruction_units",
                "scale_status": scene.metadata.get("scale_status"),
                "quality_status": scene.metadata.get("quality_status"),
            },
            indent=2,
        )
        + "\n"
    )
    print(
        f"Saved nine pair overlays, overview, PNG/SVG motion plot and diagnostics.json to {output}"
    )
    if args.show:
        plt.show()
    else:
        plt.close("all")


if __name__ == "__main__":
    main()
