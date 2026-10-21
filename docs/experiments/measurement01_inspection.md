# First Android recording: dataset inspection

M2 was verified against the locally copied `data/raw/measurement01` recording.
Raw photos and sensor data remain ignored by Git; the following are aggregate
diagnostics from `inspect_session.py`.

| Item | Result |
| --- | --- |
| Finalization | Complete, user ended |
| Duration | 18.753644 seconds |
| Successful photos | 5 |
| Stored JPEG dimensions | 4096 × 3072 for every photo |
| EXIF rotation | 90° for every photo |
| Accelerometer | 1,856 samples; 99.23 Hz; max gap 10.17 ms |
| Gyroscope | 1,855 samples; 99.23 Hz; max gap 10.20 ms |
| Rotation vector | 1,852 samples; 99.23 Hz; max gap 10.20 ms |
| Phone-reported dropped samples | 0 |
| Validation | No errors; two timestamp warnings |

All referenced JPEGs decode with dimensions matching metadata. Sensor streams
are ordered and cover every photo request timestamp with bracketing samples well
inside the default 0.25-second limit. Renaming the export folder to
`measurement01` preserves the original session ID in its metadata.

Adjacent photo requests are separated by approximately 3.117, 2.061, 2.915, and
5.378 seconds. Request-to-save-callback latency ranges from 0.378 to 0.613 seconds.
Neither endpoint is a sensor exposure timestamp, so these values do not establish
shutter delay or exact visual/IMU alignment. Both timestamp warnings are expected
for the M1 capture implementation.

Generated JSON diagnostics and the acceleration plot belong under
`data/processed/measurement01/`, where they remain local. This inspection verifies
recording consistency; reconstruction quality and measurement accuracy are not
yet evaluated.
