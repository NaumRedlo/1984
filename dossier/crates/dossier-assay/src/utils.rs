//! The handful of curves ppy's difficulty code is written in terms of.
//!
//! Ported from `DiffUtils`. They are here rather than inlined at each use
//! because the formulas quote them by name — a skill reads "logistic of the
//! ratio, midpoint 0.88, growth 10, capped at 1.1" — and a reader checking this
//! against the C# should find the same names in the same places.

/// An S-curve: rises from zero to `max_value`, passing half-way at
/// `midpoint_offset`, as steeply as `multiplier` says.
///
/// ```csharp
/// public static double Logistic(double x, double midpointOffset, double multiplier, double maxValue = 1)
///     => maxValue / (1 + Math.Exp(multiplier * (midpointOffset - x)));
/// ```
pub fn logistic(x: f64, midpoint_offset: f64, multiplier: f64, max_value: f64) -> f64 {
    max_value / (1.0 + (multiplier * (midpoint_offset - x)).exp())
}

/// Where `x` sits between `start` and `end`, clamped to the ends.
///
/// `start` may be the larger of the two, which is how it is used to fade
/// something *out* as a count rises.
pub fn reverse_lerp(x: f64, start: f64, end: f64) -> f64 {
    ((x - start) / (end - start)).clamp(0.0, 1.0)
}

/// A bell over `[0, 1]`: one in the middle, nothing at either end, smooth
/// throughout.
///
/// ```csharp
/// public static double SmoothstepBellCurve(double x)
/// {
///     x = 0.5 - Math.Abs(x - 0.5);
///     x = Math.Clamp(x * 2.0, 0.0, 1.0);
///     return x * x * (3.0 - 2.0 * x);
/// }
/// ```
pub fn smoothstep_bell_curve(x: f64) -> f64 {
    let x = 0.5 - (x - 0.5).abs();
    let x = (x * 2.0).clamp(0.0, 1.0);
    x * x * (3.0 - 2.0 * x)
}

/// Beats per minute from the gap between two objects, counting in
/// sixteenth-of-a-bar steps the way the game's rhythm arithmetic does.
pub fn milliseconds_to_bpm(ms: f64) -> f64 {
    60_000.0 / (ms * 4.0)
}

/// The same, backwards.
pub fn bpm_to_milliseconds(bpm: f64) -> f64 {
    60_000.0 / 4.0 / bpm
}
