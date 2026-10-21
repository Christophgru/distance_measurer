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
They are initially empty; later milestones add their implementations. Tests are
organized into unit, integration, and synthetic geometry suites.
