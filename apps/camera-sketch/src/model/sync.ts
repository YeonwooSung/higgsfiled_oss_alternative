/**
 * Synced result playback (spec §4 result screen): one leader video drives the others. Followers mirror
 * play/pause/rate and are re-seeked only when they drift more than one output frame (1/24 s) from
 * the leader, so normal decoding jitter never causes seek storms.
 */

/** One output frame at 24 fps: the drift tolerance. */
export const FRAME_S = 1 / 24;

/** The subset of HTMLMediaElement the sync rule uses (a fake in tests). */
export interface MediaLike {
  currentTime: number;
  paused: boolean;
  playbackRate: number;
  readyState: number;
  duration: number;
  play(): Promise<void> | void;
  pause(): void;
}

/** True when the follower is more than `tol` seconds away from the leader. */
export function needsSeek(leaderT: number, followerT: number, tol = FRAME_S): boolean {
  return Math.abs(leaderT - followerT) > tol + 1e-9;
}

/**
 * Bring one follower in line with the leader: rate, play/pause state, then time (only past the drift
 * tolerance; the target is clamped to the follower's own duration). Followers without metadata
 * (`readyState` 0) are skipped. Returns whether the follower was seeked.
 */
export function mirrorFollower(leader: MediaLike, f: MediaLike, tol = FRAME_S): boolean {
  if (f.readyState < 1) return false;
  if (f.playbackRate !== leader.playbackRate) f.playbackRate = leader.playbackRate;
  if (leader.paused && !f.paused) f.pause();
  else if (!leader.paused && f.paused) void Promise.resolve(f.play()).catch(() => {});
  const dur = Number.isFinite(f.duration) && f.duration > 0 ? f.duration : Infinity;
  const target = Math.min(Math.max(0, leader.currentTime), dur);
  if (!needsSeek(target, f.currentTime, tol)) return false;
  f.currentTime = target;
  return true;
}
