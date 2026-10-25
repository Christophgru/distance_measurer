"""Observed capture settings; missing values never establish stability."""

import math

from .schema import Session

SETTING_FIELDS = (
    "camera_id",
    "physical_camera_id",
    "focal_length_mm",
    "focal_length_35mm",
    "width",
    "height",
    "exif_orientation",
    "rotation_degrees",
    "is_flipped",
    "target_rotation",
    "lens_facing",
    "sensor_orientation_degrees",
    "exposure_time_s",
    "iso",
    "focus_distance_diopters",
    "zoom_ratio",
    "crop_region",
    "ae_mode",
    "ae_state",
    "ae_lock",
    "awb_mode",
    "awb_state",
    "awb_lock",
    "af_mode",
    "af_state",
    "lens_state",
    "sensor_timestamp_ns",
    "sensor_timestamp_clock_domain",
    "capture_result_source",
)
# Frame timestamps and algorithm states are reported but naturally vary per frame.
STABILITY_FIELDS = tuple(
    name
    for name in SETTING_FIELDS
    if name
    not in {
        "sensor_timestamp_ns",
        "capture_result_source",
        "ae_state",
        "awb_state",
        "af_state",
        "lens_state",
    }
)


def _same(first, second):
    if type(first) is float and type(second) is float:
        # Exposure is in seconds; geometry quantities use mm, diopters or a ratio.
        return math.isclose(first, second, rel_tol=1e-5, abs_tol=1e-9)
    return first == second


def summarize_capture_geometry(session: Session) -> dict:
    images = [
        {
            "index": image.index,
            "settings": {name: getattr(image.metadata, name) for name in SETTING_FIELDS},
            "capture_stability": image.metadata.raw.get("capture_stability"),
        }
        for image in session.images
    ]
    fields, warnings = {}, []
    for name in STABILITY_FIELDS:
        known = [
            (image["index"], image["settings"][name])
            for image in images
            if image["settings"][name] is not None
        ]
        changed = any(not _same(known[0][1], value) for _, value in known[1:])
        status = (
            "changed"
            if changed
            else ("stable" if len(known) == len(images) and len(known) >= 2 else "unknown")
        )
        fields[name] = {"status": status, "known_image_ids": [i for i, _ in known]}
        if changed:
            warnings.append(
                {
                    "code": "camera_settings_changed",
                    "message": f"{name} changes across recorded images {[i for i, _ in known]}",
                }
            )

    def combined(*names):
        statuses = [fields[name]["status"] for name in names]
        return (
            "changed"
            if "changed" in statuses
            else ("stable" if all(s == "stable" for s in statuses) else "unknown")
        )

    # A crop rectangle is a sufficient zoom/crop equivalent on pre-API-30 devices.
    crop_fields = ("crop_region",) + (
        ("zoom_ratio",)
        if any(image["settings"]["zoom_ratio"] is not None for image in images)
        else ()
    )
    stability = {
        "lens": combined("camera_id", "physical_camera_id"),
        "focal_length": fields["focal_length_mm"]["status"],
        "focus": fields["focus_distance_diopters"]["status"],
        "crop": combined(*crop_fields),
    }
    return {"images": images, "fields": fields, "stability": stability, "warnings": warnings}
