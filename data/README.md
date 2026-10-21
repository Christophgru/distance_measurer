# Local datasets

- `raw/<session_id>/`: immutable sessions copied from the Android capture app.
- `processed/<session_id>/`: generated reconstructions and diagnostics.

Dataset contents are ignored by Git; only the directories' `.gitkeep` files are
tracked. Keep small, redistributable regression fixtures under `python/tests/`
instead of committing personal captures here. Never modify a raw recording as
part of processing it.
