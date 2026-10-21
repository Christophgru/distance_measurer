"""Bundle-adjust M6 cameras/points with fixed first pose and first-baseline scale."""

import argparse
import sys
from pathlib import Path

from distance_measuring.optimization.bundle import LOSSES
from distance_measuring.optimization.pipeline import refine_scene


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--loss", choices=LOSSES, default="soft_l1")
    parser.add_argument(
        "--f-scale", type=float, default=1.0, help="Robust loss scale in working pixels"
    )
    parser.add_argument("--max-nfev", type=int, default=300)
    args = parser.parse_args(argv)
    try:
        report = refine_scene(
            args.scene, args.output, loss=args.loss, f_scale=args.f_scale, max_nfev=args.max_nfev
        )
    except (ValueError, OSError) as error:
        print(f"Cannot refine scene: {error}", file=sys.stderr)
        return 2
    print(
        f"Reprojection RMSE: {report['before']['reprojection_rmse_px']:.6f} -> "
        f"{report['after']['reprojection_rmse_px']:.6f} working pixels"
    )
    print(
        f"Robust cost: {report['before']['robust_cost']:.6f} -> "
        f"{report['after']['robust_cost']:.6f}"
    )
    print(
        f"Accepted: {report['accepted']}; converged: {report['converged']}; "
        f"evaluations: {report['nfev']}"
    )
    print(f"Solver: {report['solver_message']}")
    print(
        f"Gauge: fixed camera {report['gauge']['fixed_camera_id']}; "
        f"baseline {report['gauge']['fixed_baseline_length']}"
    )
    print(
        "Scale: arbitrary visual units; input calibration and geometry warnings "
        "remain in scene metadata"
    )
    print(f"Scene: {args.output / 'scene.json'}")
    return 0 if report["accepted"] and report["converged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
