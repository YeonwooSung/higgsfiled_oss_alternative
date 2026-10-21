# Pocket VFX manual evaluation sheet

**Status: not yet run.** No row below has been filled in. Plate aesthetics and matte quality are not claimed by automated
scores (spec section 14); no quality figure may be quoted before this sheet is completed on a real deployment.

## Scope

3 inputs per effect (`impact_wave`, `energy_aura`, `blink_out`) = 9 runs, per render profile that is available:

- **Quick** (bundled plate): can be evaluated on the mock profile. Plate and matte columns are `-`.
- **Generated** (LTX plate -> matte): needs the `open_weights_v1` Comfy deployment with `vfx_masks_v1` / `vfx_plate_v1` (pending).
  Subject masks are SAM 2.1 there; on the mock profile they are GrabCut or fixture ground truth and say nothing about SAM.

Fill one sheet per profile and render profile (copy the table). Record the profile, the commit and the date at the top of each copy.

## Choosing the inputs

Rights-cleared clips only (own footage). 3 different clips per effect, covering a spread: a locked-off person action
(stomp, jump, clap), a handheld clip, a 9:16 phone clip, a busy background, a low-contrast subject edge. Include at least one
9:16 and one 16:9 clip overall. Blink Out inputs need a fixed camera and an empty-frame clean plate of the same scene. Record
the file name and sha256 next to the input number.

## Procedure (per row)

1. Upload the clip, trim to 1-5 s, run analysis.
2. Click the effect point (or the subject), set timing; let it track; fix lost frames with a correction if needed.
3. Render (Quick, or generate a plate and render Generated). Watch at full size twice, with the before/after compare.
4. Fill the columns below. Do not re-roll to get a better result; if you retry (new seed, correction), log it in notes.

## Columns

- **Tracking** Does the effect stay on the intended point for the whole visible window? Note lost/invalid intervals and the corrections used. good / partly / poor
- **Edge** Behind-subject occlusion and subject edge: halo, leaking over the subject, jagged or flickering mask boundary. good / partly / poor
- **Effect quality** Timing of start/peak/end, readability, look. Free text plus good / partly / poor.
- **Plate aesthetics** (Generated only) Does the generated plate look like the requested effect, on black, without text or a scene? good / partly / poor
- **Matte quality** (Generated only) Plate -> alpha: dark halo, black fringe, flicker, empty regions. Copy the matte QA (`halo_risk`, `flicker`, `empty_plate`) as shown. good / partly / poor
- **Notes** Seed, retries, runtime, GPU, anything else.
- **Failure logged** Id of the failure entry (input, config JSON) if the row failed; otherwise `-`.

## Results (copy per profile/render profile)

Profile: ____  Render profile: ____  Commit: ____  Date: ____

| Effect | Input | Tracking | Edge | Effect quality | Plate aesthetics | Matte quality | Notes | Failure logged |
|---|---|---|---|---|---|---|---|---|
| impact_wave | 1 | | | | | | | |
| impact_wave | 2 | | | | | | | |
| impact_wave | 3 | | | | | | | |
| energy_aura | 1 | | | | | | | |
| energy_aura | 2 | | | | | | | |
| energy_aura | 3 | | | | | | | |
| blink_out | 1 | | | | | | | |
| blink_out | 2 | | | | | | | |
| blink_out | 3 | | | | | | | |

## Failure log

| Id | Input (file, sha256) | Effect | Render profile | What went wrong | Config JSON |
|---|---|---|---|---|---|
| | | | | | |
