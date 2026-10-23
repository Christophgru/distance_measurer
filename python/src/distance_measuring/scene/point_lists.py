"""BSON point-list batches retain record structure without giant JSON arrays."""

import gzip
import hashlib
import json
import struct
from pathlib import Path

import numpy as np

from .storage import write_document


def read_colmap_ply(path):
    """Map native COLMAP binary PLY fields without per-vertex Python objects."""
    types = {"float": "<f4", "float32": "<f4", "double": "<f8", "uchar": "u1", "uint8": "u1"}
    fields = []
    count = None
    in_vertices = False
    with Path(path).open("rb") as f:
        if f.readline().strip() != b"ply":
            raise ValueError("Not a PLY cloud")
        if f.readline().strip() != b"format binary_little_endian 1.0":
            raise ValueError("Expected native binary little-endian PLY")
        while True:
            line = f.readline().decode("ascii").strip()
            if not line:
                raise ValueError("Truncated PLY header")
            tokens = line.split()
            if tokens[0] == "element":
                in_vertices = tokens[1] == "vertex"
                if in_vertices:
                    count = int(tokens[2])
            elif tokens[0] == "property" and in_vertices:
                if len(tokens) != 3 or tokens[1] not in types:
                    raise ValueError("Unsupported PLY vertex field")
                fields.append((tokens[2], types[tokens[1]]))
            elif line == "end_header":
                break
        offset = f.tell()
    if count is None:
        raise ValueError("PLY has no vertex count")
    dtype = np.dtype(fields)
    if Path(path).stat().st_size < offset + count * dtype.itemsize:
        raise ValueError("Truncated PLY point data")
    if count == 0:
        return np.empty(0, dtype=dtype)
    return np.memmap(path, dtype=dtype, mode="r", offset=offset, shape=(count,))


def read_visibility(path, count, image_count):
    """Decode COLMAP MVS image-index support; never confuse it with image IDs."""
    raw = Path(path).read_bytes()
    if len(raw) < 8 or struct.unpack_from("<Q", raw)[0] != count:
        raise ValueError("Point/visibility counts differ")
    if image_count > 32:
        raise ValueError("This compact viewer supports at most 32 source views")
    masks = np.zeros(count, np.uint32)
    position = 8
    unpack = struct.Struct("<I").unpack_from
    for row in range(count):
        if position + 4 > len(raw):
            raise ValueError("Truncated visibility count")
        n = unpack(raw, position)[0]
        position += 4
        if n > image_count or position + 4 * n > len(raw):
            raise ValueError("Invalid visibility record")
        value = 0
        for image in struct.unpack_from("<" + "I" * n, raw, position):
            if image >= image_count:
                raise ValueError("Visibility references an unknown image")
            value |= 1 << image
        masks[row] = value
        position += 4 * n
    if position != len(raw):
        raise ValueError("Unexpected bytes after visibility records")
    return masks


def write_cloud_point_lists(
    ply_path,
    visibility_path,
    image_ids,
    output,
    *,
    target_points,
    id_base=1,
    min_views=3,
    batch_size=50000,
):
    """Authoritative .bson lists plus gzip transport copies, with identical records."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    vertices = read_colmap_ply(ply_path)
    support = read_visibility(visibility_path, len(vertices), len(image_ids))
    xyz = np.column_stack([vertices[k] for k in ["x", "y", "z"]])
    colors = np.column_stack([vertices[k] for k in ["red", "green", "blue"]])
    normals = np.column_stack([vertices[k] for k in ["nx", "ny", "nz"]])
    bit_counts = np.array([int(v).bit_count() for v in support], np.uint8)
    keep = (
        np.isfinite(xyz).all(axis=1) & np.isfinite(normals).all(axis=1) & (bit_counts >= min_views)
    )
    keep &= np.linalg.norm(normals, axis=1) > 0.5
    available = np.flatnonzero(keep)
    chosen = (
        available
        if len(available) <= target_points
        else available[np.linspace(0, len(available) - 1, target_points, dtype=np.int64)]
    )
    if not len(chosen):
        raise ValueError("No geometrically supported fused points remain")
    if int(chosen[-1]) + id_base > 0xFFFFFFFF:
        raise ValueError("Dense IDs exceed unsigned 32-bit range")
    selected_xyz = xyz[chosen]
    manifest = {
        "schema_version": 1,
        "format": "bson-point-list-v1",
        "point_count": len(chosen),
        "target_points": target_points,
        "target_reached": len(chosen) >= target_points,
        "native_fused_point_count": len(vertices),
        "minimum_distinct_support_views": min_views,
        "eligible_point_count": len(available),
        "image_ids": image_ids,
        "shards": [],
        "coordinate_frame": "reference_world",
        "scale_status": "not_estimated_visual_only",
        "synthetic_padding_used": False,
        "bounds": {
            "all": [selected_xyz.min(axis=0).tolist(), selected_xyz.max(axis=0).tolist()],
            "overview": np.percentile(selected_xyz, [1, 99], axis=0).tolist(),
        },
        "source_sha256": {
            str(Path(p).resolve()): hashlib.sha256(Path(p).read_bytes()).hexdigest()
            for p in [ply_path, visibility_path]
        },
    }
    for start in range(0, len(chosen), batch_size):
        selected = chosen[start : start + batch_size]
        records = []
        for row in selected:
            records.append(
                {
                    "point_id": int(row) + id_base,
                    "position": xyz[row].astype(float).tolist(),
                    "color": colors[row].astype(int).tolist(),
                    "normal": normals[row].astype(float).tolist(),
                    "supporting_image_ids": [
                        i for k, i in enumerate(image_ids) if int(support[row]) & (1 << k)
                    ],
                }
            )
        document = {"schema_version": 1, "coordinate_frame": "reference_world", "points": records}
        # Aggregate point lists >100k use BSON, even when split into smaller
        # bounded batches. Field/record structure is unchanged by the encoding.
        path = write_document(
            output / f"points_{start // batch_size:04}.bson", document, point_count=len(chosen)
        )
        content = path.read_bytes()
        if len(content) > 16 * 1024 * 1024:
            raise ValueError("Reduce batch size to keep BSON documents under 16 MiB")
        compressed = path.with_suffix(".bson.gz")
        compressed.write_bytes(gzip.compress(content, compresslevel=3, mtime=0))
        manifest["shards"].append(
            {
                "file": path.name,
                "transport_file": compressed.name,
                "point_count": len(records),
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
                "transport_bytes": compressed.stat().st_size,
            }
        )
        print(f"BSON point lists: {start + len(records):,}/{len(chosen):,}", flush=True)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
