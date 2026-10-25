/**
 * Effect SFX volume: the UI shows 0–100 %, the config stores `sfx_gain_db` in −30…0 dB (linear in dB). 0 % turns the
 * SFX off (`sfx_enabled = false`) and keeps the last gain.
 */
import type { VfxAudio } from "@oc/contracts";

export const SFX_DB_MIN = -30;

export function sfxPercent(audio: Pick<VfxAudio, "sfx_enabled" | "sfx_gain_db">): number {
  if (!audio.sfx_enabled) return 0;
  return Math.max(1, Math.round(((audio.sfx_gain_db - SFX_DB_MIN) / -SFX_DB_MIN) * 100));
}

export function sfxFromPercent(pct: number): Partial<Pick<VfxAudio, "sfx_enabled" | "sfx_gain_db">> {
  const p = Math.min(Math.max(pct, 0), 100);
  if (p <= 0) return { sfx_enabled: false };
  return { sfx_enabled: true, sfx_gain_db: Math.round((SFX_DB_MIN + (-SFX_DB_MIN * p) / 100) * 10) / 10 };
}
