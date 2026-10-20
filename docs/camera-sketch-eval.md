# Camera Sketch manual evaluation sheet

**Status: not yet run.** No row below has been filled in. Cinematic quality is not claimed by automated scores
(spec section 13); no accuracy figure may be quoted before this sheet is completed on a real deployment.

## Scope

6 photos x 3 presets (`push_in`, `slide`, `small_arc`) = 18 runs, per mode that is available:

- **Geometry** (path render): can be evaluated on the mock profile.
- **Cinematic** (AI generated): needs the `open_weights_v1` Comfy deployment with calibrated control presets (pending).

Fill one sheet per mode and profile (copy the table). Record the profile, the commit, and the date at the top of each copy.

## Choosing the photos

Rights-cleared stills only. Cover a spread, e.g.: portrait subject, product on a table, landscape with sky,
indoor room with depth, subject on a depth edge, low-detail/flat scene. Include at least one 9:16 and one 16:9 source.
Record the file name and sha256 next to the photo number.

## Procedure (per row)

1. Upload the photo, run analysis, click the target.
2. Pick the preset with default numeric endpoints (no sketch edits), Generate.
3. Watch the result at full size, twice, with the path overlay on.
4. Fill the columns below. Do not re-roll to get a better result; if you retry, log the retry in notes.

## Columns

- **Direction correct?** Does the apparent camera move match the preset (push_in: approaches; slide: lateral; small_arc: arcs around the target)? yes / no / partly
- **Subject preserved?** Identity, shape and label/text of the target stay stable through the clip. yes / no / partly
- **Artifacts** Free text: stretched rubber-sheet edges, holes, flicker, ghosting, warped faces/text, etc.
- **Hole ratio max** Max per-frame hole ratio from the diagnostics (Geometry guide), as a percentage.
- **QA status** Status reported by the motion QA (`pass` / `warn` / `unknown`); copy as shown, do not interpret.
- **Notes** Anything else (seed, retries, runtime).
- **Failure logged** Link or id of the failure entry (input file, preset, config JSON) if the row failed; otherwise `-`.

## Results (copy per mode/profile)

Mode: ____  Profile: ____  Commit: ____  Date: ____

| Photo | Preset | Direction correct? | Subject preserved? | Artifacts | Hole ratio max | QA status | Notes | Failure logged |
|---|---|---|---|---|---|---|---|---|
| 1 | push_in | | | | | | | |
| 1 | slide | | | | | | | |
| 1 | small_arc | | | | | | | |
| 2 | push_in | | | | | | | |
| 2 | slide | | | | | | | |
| 2 | small_arc | | | | | | | |
| 3 | push_in | | | | | | | |
| 3 | slide | | | | | | | |
| 3 | small_arc | | | | | | | |
| 4 | push_in | | | | | | | |
| 4 | slide | | | | | | | |
| 4 | small_arc | | | | | | | |
| 5 | push_in | | | | | | | |
| 5 | slide | | | | | | | |
| 5 | small_arc | | | | | | | |
| 6 | push_in | | | | | | | |
| 6 | slide | | | | | | | |
| 6 | small_arc | | | | | | | |

## Failure log

| Id | Photo (file, sha256) | Preset | Mode | What went wrong | Config / path JSON |
|---|---|---|---|---|---|
| | | | | | |
