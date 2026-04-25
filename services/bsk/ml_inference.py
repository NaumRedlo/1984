"""
BSK ML inference — lightweight per-round winner prediction.
Uses current map weights as skill component importances.
No model loading, no I/O — pure math on already-fetched data.
"""


def predict_round_winner(
    p1_mu_aim: float, p1_mu_speed: float, p1_mu_acc: float, p1_mu_cons: float,
    p2_mu_aim: float, p2_mu_speed: float, p2_mu_acc: float, p2_mu_cons: float,
    w_aim: float, w_speed: float, w_acc: float, w_cons: float,
) -> tuple[int, float]:
    """
    Predict round winner based on weighted mu difference.
    Returns (predicted_winner: 1|2, confidence: 0.0–1.0).
    Confidence is a sigmoid of the weighted skill gap.
    """
    import math

    score1 = w_aim * p1_mu_aim + w_speed * p1_mu_speed + w_acc * p1_mu_acc + w_cons * p1_mu_cons
    score2 = w_aim * p2_mu_aim + w_speed * p2_mu_speed + w_acc * p2_mu_acc + w_cons * p2_mu_cons

    diff = score1 - score2
    # Sigmoid scaled so that 100-point gap ≈ 0.73 confidence
    confidence = 1.0 / (1.0 + math.exp(-diff / 100.0))

    if diff >= 0:
        return 1, round(confidence, 4)
    else:
        return 2, round(1.0 - confidence, 4)
