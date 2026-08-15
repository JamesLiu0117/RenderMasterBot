# Visual evaluator benchmark

`render-master benchmark-evaluator` measures whether the configured local
vision model is accurate and repeatable on labeled, hash-verified Unreal preview
runs. It separates three kinds of evidence:

1. a human-owned expected verdict and issue-category label;
2. deterministic statistics decoded directly from the preview PNG;
3. one or more strict `EvaluationReport` results from the vision model.

The benchmark does not edit a scene, apply a correction, or overwrite an
existing report. A completed run remains the immutable unit of evidence.

## Contracts

`VisualBenchmarkSuite` contains one to 64 cases. Every case points to a run
directory relative to the suite file and includes:

- accepted evaluator verdicts;
- issue categories that must be present;
- issue categories that must be absent;
- optional accepted ranges for deterministic image metrics.

Relative paths cannot contain parent (`..`) segments. This keeps a suite
portable and prevents it from escaping its declared data root.

`VisualBenchmarkReport` freezes the suite SHA-256, evaluator identity, complete
per-repetition `EvaluationReport` records, deterministic image evidence,
accuracy, stability, contradictions, and wall-clock inference duration.

## Deterministic PNG evidence

The host decodes Unreal's non-interlaced 8-bit RGB or RGBA PNG without an image
library dependency. PNG chunk CRCs and the run manifest's artifact SHA-256 are
both checked before model inference.

The report records:

- image dimensions and SHA-256;
- mean, standard deviation, 5th percentile, and 95th percentile luminance;
- dark and clipped pixel fractions;
- center and border luminance;
- an estimated foreground fraction;
- conservative blank-like, underexposed-like, and overexposed-like flags.

Foreground fraction is a background-color-distance estimate using pixels from
the outer five percent of the frame. It is useful for controlled product shots,
but it is not object segmentation and must not be treated as semantic proof.
The vision model still judges identity, material, geometry, and composition.

## Pass and contradiction rules

A case passes only when:

- every model response is valid strict JSON;
- every verdict and issue-category result matches the frozen labels;
- all repeated verdicts agree;
- every declared image metric range passes;
- preview dimensions match the source `RenderSpec`.

Contradictions are reported separately when repeated model verdicts disagree,
when a model passes a frame with a severe deterministic image flag, when a
model fails a human-approved case whose image ranges pass, or when it passes a
human-rejected case. Contradictions are evidence for review; they never trigger
an automatic scene mutation.

## Run a suite

Keep real suites and reports outside Git beside the local run directories. If
the suite lives at the data root, case paths can use `runs/<run-name>`:

```powershell
render-master benchmark-evaluator `
  "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\visual_benchmark_suite_v1.json" `
  --output "C:\Users\James\Documents\ChatGPT\Graphics AI Assistant Project\data\visual_benchmark_qwen3vl_v1.json"
```

Exit code `0` means every case passed. Exit code `2` means the benchmark ran
successfully but at least one case missed its labels, stability requirement, or
image ranges. Exit code `1` means the suite, evidence, model call, or output
operation was invalid.

Use `--model` to compare another installed vision model against the exact same
suite. Do not change labels between model runs.

## First real result

The first curated suite contains four real UE 5.7 product-preview runs and two
repetitions per case. `qwen3-vl:8b-instruct` returned eight valid strict reports
in 24.34 seconds with 1.000 verdict stability and 0.750 case accuracy.

It correctly rejected the featureless gray door baseline, accepted the catalog
prototype-grid smoke test, and accepted the fixed-exposure dark-weathered-wood
preview. It consistently passed one human-rejected hard negative: a bleached,
oblique wood preview that did not meet the requested dark, front-facing product
view. The report preserves that mismatch as a contradiction instead of allowing
the result into an unattended correction loop.

The local suite and generated reports remain under the external data root and
are not repository content. The repository stores only the reusable contract,
runner, tests, example, and documentation.

## Future training value

The suite and report together form a training-quality audit record: immutable
render evidence, human ground truth, deterministic measurements, model output,
latency, and identified hard negatives. Later SFT, LoRA, preference tuning, or
evaluator calibration can select examples from this record without granting a
training process access to Unreal project mutation.
