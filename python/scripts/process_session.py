"""Inspect a recording, reconstruct it and export its browser viewer in one run."""

import argparse
import json
import math
import os
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parent


def _command(arguments):
    return subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)


def _run_logged(command, log_path, environment, capture):
    with log_path.open("w", encoding="utf-8") as log:
        if capture:
            result = subprocess.run(
                command, capture_output=True, text=True, check=False, env=environment
            )
            log.write(result.stdout)
            log.write(result.stderr)
            if result.stderr:
                print(result.stderr, file=sys.stderr, end="")
            return result
        with subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=environment
        ) as process:
            try:
                for line in process.stdout:
                    print(line, end="", flush=True)
                    log.write(line)
                    log.flush()
                return subprocess.CompletedProcess(command, process.wait())
            except BaseException:
                process.kill()
                process.wait()
                raise


def _save_report(output, report):
    (output / "workflow.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    lines = [
        "# Recording workflow",
        "",
        f"Status: **{report['status']}**",
        f"Session: `{report['session_path']}`",
        "",
    ]
    for stage in report["stages"]:
        lines.append(
            f"- [{stage['name']}]({stage['log_file']}): "
            f"exit {stage['exit_code']} ({stage['runtime_s']:.2f} s)"
        )
    if report.get("active_stage"):
        lines += ["", f"Active stage: {report['active_stage']}"]
    if report.get("failure_reason"):
        lines += ["", report["failure_reason"]]
    if report.get("viewer"):
        lines += ["", "[Open the 3D viewer](web/index.html)"]
    if report.get("measurement_page"):
        lines += ["", "[Open endpoint review](web/measurement/index.html)"]
    lines += ["", "Geometry and measurement acceptance remain in the individual stage reports."]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--output", type=Path, help="Fresh run directory; default is timestamped")
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--image-indices", type=int, nargs="+")
    parser.add_argument("--tracking-source", choices=("auto", "stills", "hybrid"), default="auto")
    parser.add_argument("--initialize-motion", action="store_true")
    parser.add_argument("--max-image-size", type=int, default=1600)
    parser.add_argument("--max-points", type=int, default=800)
    parser.add_argument(
        "--measurement-reference-m",
        type=float,
        help="Also create blank endpoint annotations and review HTML using this known length",
    )
    parser.add_argument("--serve", action="store_true", help="Serve the exported UI until Ctrl+C")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.max_image_size < 32 or args.max_points < 1:
        parser.error("--max-image-size must be at least 32 and --max-points must be positive")
    if args.measurement_reference_m is not None and (
        not math.isfinite(args.measurement_reference_m) or args.measurement_reference_m <= 0
    ):
        parser.error("--measurement-reference-m must be positive and finite")

    output, report = None, None
    started = perf_counter()
    try:
        session = args.session.resolve(strict=True)
        calibration = args.calibration.resolve(strict=True) if args.calibration else None
        stamp = datetime.now(UTC).strftime("process_%Y%m%d_%H%M%S_%f")
        output = (
            args.output.resolve() if args.output else ROOT / "data/processed" / session.name / stamp
        ).resolve()
        if output.is_relative_to((ROOT / "data/raw").resolve()) or output.is_relative_to(session):
            raise ValueError("Run output must be outside data/raw and the raw session")
        if output.exists():
            raise ValueError(f"Output already exists: {output}; choose a fresh directory")
        output.mkdir(parents=True, exist_ok=False)
        report = {
            "schema_version": 1,
            "session_path": str(session),
            "output": str(output),
            "recorded_at_utc": datetime.now(UTC).isoformat(),
            "status": "running",
            "stages": [],
            "viewer": None,
            "measurement_page": None,
            "failure_reason": None,
            "active_stage": None,
        }
        environment = os.environ.copy()
        environment.setdefault("OPENBLAS_NUM_THREADS", "1")
        environment.setdefault("OMP_NUM_THREADS", "1")

        def run_stage(name, script, options, *, capture=False):
            command = [sys.executable, str(SCRIPTS / script), *map(str, options)]
            print(f"Workflow: {name}", flush=True)
            stage_start = perf_counter()
            report["active_stage"] = name
            _save_report(output, report)
            result = _run_logged(command, output / f"{name}.log", environment, capture)
            report["stages"].append(
                {
                    "name": name,
                    "command": command,
                    "exit_code": result.returncode,
                    "runtime_s": perf_counter() - stage_start,
                    "log_file": f"{name}.log",
                }
            )
            report["active_stage"] = None
            _save_report(output, report)
            return result

        inspection = run_stage(
            "inspection", "inspect_session.py", [session, "--json"], capture=True
        )
        summary = json.loads(inspection.stdout)
        (output / "inspection.json").write_text(
            json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        if inspection.returncode:
            details = summary.get("format_error") or "; ".join(
                error["message"] for error in summary.get("errors", [])
            )
            report.update(status="inspection_failed", failure_reason=details or "Inspection failed")
            print(f"Inspection failed: {report['failure_reason']}", file=sys.stderr)
            return 1 if inspection.returncode == 1 else 2
        print(
            f"Inspection passed: {len(summary['images'])} photos; "
            f"{len(summary['warnings'])} warnings"
        )
        reconstruction = output / "reconstruction"
        options = [
            session,
            "--output",
            reconstruction,
            "--tracking-source",
            args.tracking_source,
            "--max-image-size",
            args.max_image_size,
            "--max-points",
            args.max_points,
        ]
        if calibration:
            options += ["--calibration", calibration]
        if args.image_indices is not None:
            options += ["--image-indices", *args.image_indices]
        if args.initialize_motion:
            options += ["--initialize-motion"]
        result = run_stage("reconstruction", "reconstruct_incremental.py", options)
        if result.returncode not in (0, 1):
            raise ValueError(
                "Reconstruction failed; see reconstruction.log and any reconstruction reports"
            )
        reconstruction_report = json.loads((reconstruction / "reconstruction.json").read_text())
        scene = next(
            (
                p
                for p in (reconstruction / "scene.json", reconstruction / "scene.bson")
                if p.is_file()
            ),
            None,
        )
        report["reconstruction_status"] = reconstruction_report["status"]
        report["failure_reason"] = reconstruction_report["failure_reason"]
        if scene is None:
            if result.returncode == 0:
                raise ValueError("Reconstruction reported success without a scene")
            report["status"] = "no_scene"
            print(f"No supported scene: {report['failure_reason']}", file=sys.stderr)
            return 1
        options = [scene, "--session", session, "--output", output / "web/index.html"]
        if calibration:
            options += ["--calibration", calibration]
        if run_stage("viewer_export", "export_scene_web.py", options).returncode:
            raise ValueError("Viewer export failed; reconstruction is preserved")
        report["viewer"] = str(output / "web/index.html")
        if args.measurement_reference_m is not None:
            options = [
                scene,
                "--session",
                session,
                "--output",
                output / "measurement",
                "--review-html",
                output / "web/measurement/index.html",
                "--reference-length-m",
                args.measurement_reference_m,
            ]
            if calibration:
                options += ["--calibration", calibration]
            if run_stage("measurement_setup", "init_measurements.py", options).returncode:
                raise ValueError(
                    "Measurement setup failed; the reconstruction and viewer are preserved"
                )
            report["measurement_page"] = str(output / "web/measurement/index.html")
        report["status"] = "complete" if result.returncode == 0 else "partial"
        code = result.returncode
        command = [
            sys.executable,
            "-m",
            "http.server",
            str(args.port),
            "--bind",
            "127.0.0.1",
            "--directory",
            str(output / "web"),
        ]
        report["serve_command"] = command
        print(f"Run: {output}")
        print(f"Serve: {_command(command)}")
        print(f"Viewer URL after starting the server: http://localhost:{args.port}/")
        if report["measurement_page"]:
            print(f"Endpoint review: http://localhost:{args.port}/measurement/")
        if report["failure_reason"]:
            print(f"Partial reconstruction: {report['failure_reason']}")
        report["processing_runtime_s"] = perf_counter() - started
        _save_report(output, report)
        if args.serve:
            handler = partial(SimpleHTTPRequestHandler, directory=str(output / "web"))
            try:
                with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
                    print("Viewer server started; press Ctrl+C to stop", flush=True)
                    server.serve_forever()
            except KeyboardInterrupt:
                print("\nViewer server stopped")
        return code
    except (OSError, ValueError, KeyError, TypeError) as error:
        if report is not None:
            report.update(status="error", failure_reason=str(error))
        print(f"Cannot process session: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        if report is not None:
            report.update(
                status="interrupted",
                failure_reason="Processing interrupted; partial outputs retained",
            )
        print("\nProcessing interrupted; partial outputs retained", file=sys.stderr)
        return 130
    finally:
        if report is not None:
            report["runtime_s"] = perf_counter() - started
            _save_report(output, report)
            print(f"Workflow report: {output / 'REPORT.md'}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
