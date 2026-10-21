# Python prototype

Requires Python 3.12 or newer. From the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
cd python
python -m pip install -e '.[dev]'
python -c "import distance_measuring"
pytest
cd ..
ruff check .
ruff format --check .
```

The base install (`python -m pip install -e .`) includes the initial scientific
dependencies and pytest. The `dev` extra also installs Ruff. Ruff configuration
is at the repository root so the same rules apply to package code, scripts, and
supporting tools.

The package uses a `src/` layout. Subpackages separate dataset loading, camera
models, computer vision, optimization, IMU processing, scene data, and viewing.
The dataset package loads and validates captures; later milestones add the
reconstruction modules. Tests are organized into unit, integration, and synthetic
geometry suites.

## Inspect a captured session

With the environment active, run from the repository root:

```bash
python python/scripts/inspect_session.py data/raw/measurement01
python python/scripts/inspect_session.py data/raw/measurement01 \
    --plot data/processed/measurement01/acceleration.png
```

Use `--json` for machine-readable output. Exit status is 0 for valid sessions
(warnings are allowed), 1 for consistency errors, and 2 for malformed files or
command options. Sensor coverage requires samples on both sides of each image;
`--max-sensor-gap-s` changes the default 0.25-second bracketing interval limit.
Validation fully decodes every referenced image and checks its stored dimensions
before EXIF rotation. It accepts any number of images and keeps the folder name
independent of the recorded session ID.

```python
from distance_measuring.dataset import load_session, validate_session

session = load_session("data/raw/measurement01")  # validates by default
print(session.metadata.session_id, session.duration_s)
print(len(session.images), len(session.sensor_streams["accelerometer"]))
report = validate_session(session)
for warning in report.warnings:
    print(warning.code, warning.message)
```

See [dataset_inspection.md](../docs/architecture/dataset_inspection.md) for error
handling, schema details, and interpretation of diagnostics.
