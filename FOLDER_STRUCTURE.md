# Proposed Folder Structure

```text
distance-measuring/
├── README.md
├── TODO.md
├── .gitignore
├── LICENSE
│
├── android/
│   ├── app/
│   ├── build.gradle.kts
│   ├── settings.gradle.kts
│   └── README.md
│
├── python/
│   ├── pyproject.toml
│   ├── src/
│   │   └── distance_measuring/
│   │       ├── dataset/
│   │       ├── camera/
│   │       ├── vision/
│   │       ├── optimization/
│   │       ├── imu/
│   │       ├── scene/
│   │       └── visualization/
│   ├── scripts/
│   └── tests/
│       ├── unit/
│       ├── integration/
│       └── synthetic/
│
├── data/
│   ├── raw/
│   ├── processed/
│   └── README.md
│
├── docs/
│   ├── architecture/
│   ├── math/
│   ├── experiments/
│   └── decisions/
│
├── tools/
│   ├── calibration/
│   └── dataset/
│
└── local/
    └── lecture/
```

## Ownership

- `android/`: capture only.
- `python/`: reconstruction, optimization, scale recovery, visualization.
- `data/raw/`: immutable phone sessions; ignored by Git.
- `data/processed/`: generated results; ignored by Git.
- `docs/`: project-owned documentation safe to commit.
- `local/lecture/`: copyrighted/private lecture material; ignored by Git.
- `tools/`: supporting utilities that are not runtime package code.
