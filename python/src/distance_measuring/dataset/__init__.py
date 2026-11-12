"""Capture session schemas, loading, and validation."""

from .loader import load_session
from .schema import (
    DatasetFormatError,
    ImageEvent,
    ImageMetadata,
    SensorSample,
    Session,
    SessionMetadata,
)
from .tracking import TrackingEvent, TrackingFrame, TrackingRecording
from .validation import SessionValidationError, ValidationReport, validate_session

__all__ = [
    "DatasetFormatError",
    "ImageEvent",
    "ImageMetadata",
    "SensorSample",
    "Session",
    "SessionMetadata",
    "TrackingEvent",
    "TrackingFrame",
    "TrackingRecording",
    "SessionValidationError",
    "ValidationReport",
    "load_session",
    "validate_session",
]
