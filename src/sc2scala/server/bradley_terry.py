"""Bradley-Terry ratings on the Elo scale: player A beats player B with probability `1 / (1 + 10^((R_B - R_A) / 400))`.

The ratings are the most likely ones given the games and a normal prior on each, of mean 0 and the width given, which
keeps a player of few games near the middle; they then average 0 exactly. A rating's standard error comes from the
posterior's curvature there, and is that of the rating less the players' mean: how far a player stands from the others
is known far better than where they all stand. A tie counts half a win for each player.
"""

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

_NATURAL_UNITS_PER_ELO_POINT = math.log(10) / 400
_MAX_NEWTON_STEPS = 100
_TOLERANCE = 1e-10


@dataclass(frozen=True)
class FittedRatings:
    """Each player's rating and its standard error, in Elo points, by the player's index."""

    ratings: npt.NDArray[np.float64]
    standard_errors: npt.NDArray[np.float64]


def fit_ratings(
    player_count: int,
    first: npt.ArrayLike,
    second: npt.ArrayLike,
    score: npt.ArrayLike,
    *,
    prior_sd: float,
) -> FittedRatings:
    """The ratings of players `0` to `player_count - 1`, given games between `first[k]` and `second[k]` in which
    `first[k]` scored `score[k]`, 1, ½ or 0, under a prior of standard deviation `prior_sd` Elo points."""
    first_players = np.asarray(first, dtype=np.intp)
    second_players = np.asarray(second, dtype=np.intp)
    scores = np.asarray(score, dtype=np.float64)
    prior_precision = 1 / (prior_sd * _NATURAL_UNITS_PER_ELO_POINT) ** 2
    strengths = np.zeros(player_count)
    for _ in range(_MAX_NEWTON_STEPS):
        gradient, hessian = _gradient_and_hessian(strengths, first_players, second_players, scores, prior_precision)
        step = np.linalg.solve(hessian, gradient)
        strengths -= step
        if np.max(np.abs(step), initial=0.0) < _TOLERANCE:
            break
    _, hessian = _gradient_and_hessian(strengths, first_players, second_players, scores, prior_precision)
    covariance = np.linalg.inv(-hessian)
    centering = np.eye(player_count) - 1 / player_count
    variances = np.diag(centering @ covariance @ centering)
    return FittedRatings(
        ratings=strengths / _NATURAL_UNITS_PER_ELO_POINT,
        standard_errors=np.sqrt(variances) / _NATURAL_UNITS_PER_ELO_POINT,
    )


def win_probability(rating_difference: npt.ArrayLike) -> npt.NDArray[np.float64]:
    """The probability that a player beats one rated `rating_difference` Elo points below."""
    return _logistic(np.asarray(rating_difference, dtype=np.float64) * _NATURAL_UNITS_PER_ELO_POINT)


def _gradient_and_hessian(
    strengths: npt.NDArray[np.float64],
    first: npt.NDArray[np.intp],
    second: npt.NDArray[np.intp],
    scores: npt.NDArray[np.float64],
    prior_precision: float,
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """The gradient and Hessian of the log posterior, in natural units, at `strengths`."""
    player_count = len(strengths)
    expected = _logistic(strengths[first] - strengths[second])
    surprise = scores - expected
    gradient = (
        np.bincount(first, surprise, player_count)
        - np.bincount(second, surprise, player_count)
        - prior_precision * strengths
    )
    information = expected * (1 - expected)
    hessian = np.zeros((player_count, player_count))
    np.add.at(hessian, (first, second), information)
    np.add.at(hessian, (second, first), information)
    played = np.bincount(first, information, player_count) + np.bincount(second, information, player_count)
    hessian[np.diag_indices(player_count)] -= played + prior_precision
    return gradient, hessian


def _logistic(values: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    return 0.5 * (1 + np.tanh(values / 2))
