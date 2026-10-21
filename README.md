# Ustaad

**Record a physical skill once on Ray-Ban Meta glasses. Coach anyone through it, live, in the expert's own voice.**

An expert wears the glasses and does a task while talking normally: making masala chai, assembling a shelf, fixing a bicycle puncture. Ustaad compiles that one first-person recording into a **skill file**: a graph of steps, what "done" looks like for each one, the mistakes the expert warned about, and the expert's own voice clips. Later, anyone wearing the glasses gets coached through the same task: Ustaad follows their progress, speaks the next step in the expert's voice, and catches skipped or out-of-order steps.

> **Status:** design phase. Start with the [design document](docs/DESIGN.md).

## What's here

| Path | What it is |
|---|---|
| [`docs/DESIGN.md`](docs/DESIGN.md) | Architecture, Teach and Learn pipelines, step tracker, model and API choices, privacy and compliance, evaluation plan, milestones |
| [`schemas/skill.schema.json`](schemas/skill.schema.json) | The skill file format (JSON Schema 2020-12) |
| [`examples/masala-chai/skill.json`](examples/masala-chai/skill.json) | A complete example skill in Hindi and English (asset hashes are placeholders) |
| [`tools/validate_skill.py`](tools/validate_skill.py) | Schema validation plus the cross-reference checks a schema can't express |

## Validate a skill

```bash
pip install "jsonschema>=4.18" rfc3339-validator
python tools/validate_skill.py examples/masala-chai/skill.json
```

The validator checks the schema and then the invariants: unique IDs, dependencies that form a DAG, order mistakes that agree with the graph, assets that exist with the right type and embedding size, every string covering every available language, ordered thresholds, and source spans that fit the recording.

## Built on

- Meta Wearables Device Access Toolkit 1.0 (camera, motion, on-device speech, voice launch)
- DINOv2 embeddings on device with ExecuTorch
- Gemini and Claude for step drafting and verification, behind a provider-agnostic gateway
- Sarvam `saaras:v4` and Bulbul for Indian-language speech
