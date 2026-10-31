// Upstream particle-field.tsx type-only excerpts.
type Range = readonly [min: number, max: number]
export interface ParticleFieldConfig {
  /** Particles per burst when `burst()` is called without a count. */
  count: number
  /** Window (ms) over which a burst releases its particles — small = one poof. */
  spawnWindowMs: number
  /** Glyph edge size (px), uniform. */
  size: Range
  /** Vertical travel before fade-out (% of field height), `life`-biased. */
  rise: Range
  /** Rise duration (ms), `life`-biased so short-lived particles also rise less. */
  duration: Range
  /** Sway amplitude each side of center (px), uniform. */
  swayAmp: Range
  /** Peak tilt into the sway (deg), uniform. */
  bank: Range
  /** Sway period (ms), uniform — independent of the rise so paths never repeat. */
  swayDuration: Range
  /** Cap on simultaneously-alive particles. */
  maxAlive: number
}