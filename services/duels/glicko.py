import math
from dataclasses import dataclass

SCALE = 173.7178
START_RATING = 1500.0
START_RD = 350.0
START_SIGMA = 0.06
TAU = 0.5
RD_MOST = 350.0
EPSILON = 0.000001
STEPS_MOST = 1000

class RatingFailure(ArithmeticError):
    pass

@dataclass(frozen=True)
class Rating:
    rating: float = START_RATING
    rd: float = START_RD
    sigma: float = START_SIGMA

@dataclass(frozen=True)
class Game:
    opponent: Rating
    score: float

def _finite(*values: float) -> None:
    if not all(math.isfinite(value) for value in values):
        raise RatingFailure("a value stopped being a number")

def _g(phi: float) -> float:
    return 1 / math.sqrt(1 + 3 * phi * phi / (math.pi * math.pi))

def _volatility(sigma: float, phi: float, v: float, delta: float, tau: float) -> float:
    a = math.log(sigma * sigma)

    def f(x: float) -> float:
        ex = math.exp(x)
        return ex * (delta * delta - phi * phi - v - ex) / (2 * (phi * phi + v + ex) ** 2) - (x - a) / (tau * tau)

    big_a = a
    if delta * delta > phi * phi + v:
        big_b = math.log(delta * delta - phi * phi - v)
    else:
        k = 1
        while f(a - k * tau) < 0:
            k += 1
            if k > STEPS_MOST:
                raise RatingFailure("no bracket for volatility")
        big_b = a - k * tau
    f_a, f_b = f(big_a), f(big_b)
    steps = 0
    while abs(big_b - big_a) > EPSILON:
        steps += 1
        if steps > STEPS_MOST or f_b == f_a:
            raise RatingFailure("volatility did not settle")
        big_c = big_a + (big_a - big_b) * f_a / (f_b - f_a)
        f_c = f(big_c)
        _finite(big_c, f_c)
        if f_c * f_b <= 0:
            big_a, f_a = big_b, f_b
        else:
            f_a /= 2
        big_b, f_b = big_c, f_c
    return math.exp(big_a / 2)

def idle(player: Rating, periods: int = 1) -> Rating:
    phi = player.rd / SCALE
    grown = math.sqrt(phi * phi + periods * player.sigma * player.sigma) * SCALE
    return Rating(player.rating, min(RD_MOST, grown), player.sigma)

def update(player: Rating, games: list[Game], tau: float = TAU) -> Rating:
    if not games:
        return idle(player)
    mu = (player.rating - START_RATING) / SCALE
    phi = player.rd / SCALE
    total, impact = 0.0, 0.0
    for game in games:
        mu_j = (game.opponent.rating - START_RATING) / SCALE
        g = _g(game.opponent.rd / SCALE)
        expected = 1 / (1 + math.exp(-g * (mu - mu_j)))
        total += g * g * expected * (1 - expected)
        impact += g * (game.score - expected)
    if total <= 0:
        raise RatingFailure("no information in the games")
    v = 1 / total
    delta = v * impact
    sigma = _volatility(player.sigma, phi, v, delta, tau)
    phi_star = math.sqrt(phi * phi + sigma * sigma)
    phi_new = 1 / math.sqrt(1 / (phi_star * phi_star) + 1 / v)
    mu_new = mu + phi_new * phi_new * impact
    rating, rd = SCALE * mu_new + START_RATING, min(RD_MOST, SCALE * phi_new)
    _finite(rating, rd, sigma)
    return Rating(rating, rd, sigma)

def closeness(first: float, second: float) -> float:
    return 1 / (1 + 10 ** ((second - first) / 400))
