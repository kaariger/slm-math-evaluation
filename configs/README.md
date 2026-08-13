# Configuration

Evaluation inputs are separated so a material change can create a new,
identifiable run rather than silently rewriting an old result:

- `models/`: model identity, immutable revision, and runtime-bound settings
- `datasets/`: dataset identity, split, revision, loader, and scorer
- `prompts/`: versioned prompt protocol and answer-format instructions
- `generation/`: versioned decoding settings and seed policy

The current files are design templates, not runnable configurations. Fields and
validation should be finalized alongside the first real vertical path, after a
local runtime and reference method have been explicitly selected.
