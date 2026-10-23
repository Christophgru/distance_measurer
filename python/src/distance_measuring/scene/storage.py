"""JSON/BSON encoding with identical document structure and bounded point batches."""

import json
from pathlib import Path

JSON_POINT_LIMIT = 100_000


def decode_document(content, suffix):
    if suffix.lower() == ".bson":
        from bson import BSON, Int64
        from bson.codec_options import CodecOptions, TypeDecoder, TypeRegistry

        class IntegerDecoder(TypeDecoder):
            bson_type = Int64

            def transform_bson(self, value):
                return int(value)

        return BSON(content).decode(
            codec_options=CodecOptions(type_registry=TypeRegistry([IntegerDecoder()]))
        )
    return json.loads(content)


def read_document(path):
    path = Path(path)
    return decode_document(path.read_bytes(), path.suffix)


def write_document(path, document, *, point_count=None):
    """Return actual path; point lists over 100k always use a .bson suffix."""
    path = Path(path)
    count = len(document.get("points", [])) if point_count is None else point_count
    if count > JSON_POINT_LIMIT or path.suffix.lower() == ".bson":
        from bson import BSON

        path = path.with_suffix(".bson")
        content = BSON.encode(document)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2, allow_nan=False) + "\n")
    return path
