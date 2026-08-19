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

/// The same curve stated by its exponent alone.
///
/// ```csharp
/// public static double Logistic(double exponent, double maxValue = 1) => maxValue / (1 + Math.Exp(exponent));
/// ```
///
/// ppy have both overloads and they are not the same function with different
/// defaults — this one takes the exponent already formed, and feeding it to the
/// four-argument form flips its sign.
pub fn logistic_of(exponent: f64, max_value: f64) -> f64 {
    max_value / (1.0 + exponent.exp())
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

/// An S-curve between two points: nothing below `start`, one above `end`,
/// smooth at both ends.
///
/// `start` may be the larger of the two, which reads as "fades out as x rises".
pub fn smoothstep(x: f64, start: f64, end: f64) -> f64 {
    let x = ((x - start) / (end - start)).clamp(0.0, 1.0);
    x * x * (3.0 - 2.0 * x)
}

/// The same, flatter still at both ends — the fifth-order form.
pub fn smootherstep(x: f64, start: f64, end: f64) -> f64 {
    let x = ((x - start) / (end - start)).clamp(0.0, 1.0);
    x * x * x * (x * (6.0 * x - 15.0) + 10.0)
}

/// The p-norm of a vector: `(Σ xᵢᵖ)^(1/p)`.
///
/// Used to add two difficulties together in a way that is neither "the larger
/// one" nor "both of them": at p above one, a value that is high in both counts
/// for more than either alone but less than their sum.
pub fn norm(p: f64, values: &[f64]) -> f64 {
    values.iter().map(|x| x.powf(p)).sum::<f64>().powf(1.0 / p)
}

/// Beats per minute from the gap between two objects.
///
/// `delimiter` is which subdivision is being counted — four for sixteenths,
/// which is the default the rhythm arithmetic uses, two for eighths.
pub fn milliseconds_to_bpm_at(ms: f64, delimiter: f64) -> f64 {
    60_000.0 / (ms * delimiter)
}

/// The same, counting in sixteenth-of-a-bar steps.
pub fn milliseconds_to_bpm(ms: f64) -> f64 {
    milliseconds_to_bpm_at(ms, 4.0)
}

/// The same, backwards.
pub fn bpm_to_milliseconds(bpm: f64) -> f64 {
    60_000.0 / 4.0 / bpm
}

/// √2, spelled out because ppy spell it out.
pub const SQRT2: f64 = 1.4142135623730950;

/// The error function — the share of a normal distribution within `x` standard
/// deviations, near enough.
///
/// Abramowitz and Stegun 7.1.26, which is ppy's choice and so must be this
/// port's: it is an *approximation*, accurate to about a part in ten million,
/// and substituting a better one would put us next to ppy rather than on them.
pub fn erf(x: f64) -> f64 {
    if x == 0.0 {
        return 0.0;
    }
    if x.is_nan() {
        return f64::NAN;
    }
    if x.is_infinite() {
        return x.signum();
    }
    let t = 1.0 / (1.0 + 0.327_591_1 * x.abs());
    let tau = t
        * (0.254_829_592
            + t * (-0.284_496_736
                + t * (1.421_413_741 + t * (-1.453_152_027 + t * 1.061_405_429))));
    let value = 1.0 - tau * (-x * x).exp();
    if x >= 0.0 { value } else { -value }
}

/// The error function backwards: how many standard deviations hold `x` of the
/// distribution.
///
/// Winitzki's approximation with ppy's correction term above 0.85, which they
/// note takes the worst error from -0.005 to -0.00045. Ported for the same
/// reason as [`erf`] — the goal is their answer, not the true one.
pub fn erf_inv(x: f64) -> f64 {
    if x <= -1.0 {
        return f64::NEG_INFINITY;
    }
    if x >= 1.0 {
        return f64::INFINITY;
    }
    if x == 0.0 {
        return 0.0;
    }
    const A: f64 = 0.147;
    let sign = x.signum();
    let x = x.abs();

    let ln = (1.0 - x * x).ln();
    let t1 = 2.0 / (std::f64::consts::PI * A) + ln / 2.0;
    let t2 = ln / A;
    let base = (t1 * t1 - t2).sqrt() - t1;

    let correction = if x >= 0.85 { ((x - 0.85) / 0.293).powi(8) } else { 0.0 };
    sign * (base.sqrt() + correction)
}
