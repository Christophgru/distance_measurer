import gzip
import struct

import numpy as np
import pytest

from distance_measuring.scene.point_lists import (
    read_colmap_ply,
    read_visibility,
    write_cloud_point_lists,
)
from distance_measuring.scene.storage import read_document


def cloud(tmp_path):
    fields = [(k, "<f4") for k in ["x", "y", "z", "nx", "ny", "nz"]]
    fields += [(k, "u1") for k in ["red", "green", "blue"]]
    vertices = np.zeros(5, dtype=fields)
    vertices["x"] = np.arange(5)
    vertices["nz"] = 1
    vertices["red"] = 123
    vertices["x"][4] = np.nan
    header = "ply\nformat binary_little_endian 1.0\nelement vertex 5\n"
    header += "".join(f"property {'uchar' if t == 'u1' else 'float'} {k}\n" for k, t in fields)
    ply = tmp_path / "fused.ply"
    ply.write_bytes(header.encode() + b"end_header\n" + vertices.tobytes())
    vis = tmp_path / "fused.ply.vis"
    records = [[0, 1, 2], [2, 0, 1], [0, 0, 1], [0, 1, 2], [0, 1, 2]]
    vis.write_bytes(
        struct.pack("<Q", 5)
        + b"".join(struct.pack("<I", len(r)) + struct.pack("<" + "I" * len(r), *r) for r in records)
    )
    return ply, vis


def test_bson_batches_preserve_records_and_distinct_camera_support(tmp_path):
    ply, vis = cloud(tmp_path)
    result = write_cloud_point_lists(
        ply, vis, [7, 2, 3], tmp_path / "cloud", target_points=10, id_base=100, batch_size=2
    )
    assert result["point_count"] == 3
    assert not result["target_reached"]
    assert not result["synthetic_padding_used"]
    rows = []
    for shard in result["shards"]:
        path = tmp_path / "cloud" / shard["file"]
        assert gzip.decompress(path.with_suffix(".bson.gz").read_bytes()) == path.read_bytes()
        rows.extend(read_document(path)["points"])
    assert [r["point_id"] for r in rows] == [100, 101, 103]
    assert rows[0] == {
        "point_id": 100,
        "position": [0.0, 0.0, 0.0],
        "normal": [0.0, 0.0, 1.0],
        "color": [123, 0, 0],
        "supporting_image_ids": [7, 2, 3],
    }


def test_visibility_rejects_truncation_and_unknown_camera(tmp_path):
    _, vis = cloud(tmp_path)
    original = vis.read_bytes()
    vis.write_bytes(original[:-1])
    with pytest.raises(ValueError, match="Invalid visibility"):
        read_visibility(vis, 5, 3)
    invalid = bytearray(original)
    struct.pack_into("<I", invalid, 12, 3)
    vis.write_bytes(invalid)
    with pytest.raises(ValueError, match="unknown image"):
        read_visibility(vis, 5, 3)


def test_exact_duplicate_positions_do_not_pad_target(tmp_path):
    ply, vis = cloud(tmp_path)
    vertices = read_colmap_ply(ply)
    writable = np.memmap(ply, mode="r+", dtype=vertices.dtype, offset=vertices.offset, shape=(5,))
    writable["x"][3] = 0
    writable.flush()
    result = write_cloud_point_lists(ply, vis, [7, 2, 3], tmp_path / "cloud", target_points=10)
    assert result["point_count"] == 2
    assert result["duplicate_positions_removed"] == 1
