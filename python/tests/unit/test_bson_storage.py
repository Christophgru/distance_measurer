import json

import pytest

from distance_measuring.scene import load_scene, save_scene
from distance_measuring.scene.storage import read_document, write_document


@pytest.mark.parametrize("count", [100000, 100001])
def test_large_point_lists_switch_encoding_without_changing_records(tmp_path, count):
    document = {
        "schema_version": 1,
        "points": [{"point_id": i, "position": [1.25, -2.5, 3.75]} for i in range(count)],
        "metadata": {"timestamp_ns": 2**60 + 3, "value": True},
    }
    path = write_document(tmp_path / "points.json", document)
    assert path.suffix == (".bson" if count > 100000 else ".json")
    decoded = read_document(path)
    assert decoded == document
    assert type(decoded["metadata"]["timestamp_ns"]) is int
    if count > 100000:
        assert not (tmp_path / "points.json").exists()


def test_same_scene_schema_roundtrips_in_json_and_bson(tmp_path, bundle_scene_factory):
    scene, _, _ = bundle_scene_factory()
    json_path = save_scene(tmp_path / "scene.json", scene)
    bson_path = save_scene(tmp_path / "scene.bson", scene)
    assert load_scene(json_path).to_dict() == load_scene(bson_path).to_dict() == scene.to_dict()
    assert json.loads(json_path.read_text()) == read_document(bson_path)
