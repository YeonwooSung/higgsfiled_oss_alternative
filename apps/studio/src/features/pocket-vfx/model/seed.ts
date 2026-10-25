/** Plate seed field (API `SEED_MAX` = 2^31 - 1). */
export const SEED_MAX = 2 ** 31 - 1;

/** The seed typed in the field, or null for an empty / partial / non-integer entry (the saved seed is kept). */
export function parseSeed(raw: string): number | null {
  const s = raw.trim();
  if (!/^\d+$/.test(s)) return null;
  return Math.min(Number(s), SEED_MAX);
}
