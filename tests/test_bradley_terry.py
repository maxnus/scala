import numpy as np
import pytest

from sc2scala.server.bradley_terry import fit_ratings, win_probability


def test_win_probability_is_elos() -> None:
    assert win_probability(0) == pytest.approx(0.5)
    assert win_probability(400) == pytest.approx(10 / 11)
    assert win_probability(-400) == pytest.approx(1 / 11)


def test_ratings_drawn_from_known_strengths_are_recovered() -> None:
    rng = np.random.default_rng(1)
    true_ratings = np.array([-300.0, -100.0, 0.0, 150.0, 250.0])
    first = rng.integers(0, 5, 20_000)
    second = (first + rng.integers(1, 5, 20_000)) % 5
    score = (rng.random(20_000) < win_probability(true_ratings[first] - true_ratings[second])).astype(float)
    fitted = fit_ratings(5, first, second, score, prior_sd=1000)
    recovered = fitted.ratings - fitted.ratings.mean()
    assert np.allclose(recovered, true_ratings - true_ratings.mean(), atol=25)
    assert np.all(fitted.standard_errors < 15)


def test_the_prior_keeps_a_player_of_few_games_near_the_middle() -> None:
    fitted = fit_ratings(2, [0], [1], [1.0], prior_sd=100)
    assert 0 < fitted.ratings[0] < 50
    assert fitted.ratings[1] == pytest.approx(-fitted.ratings[0])
    wide = fit_ratings(2, [0], [1], [1.0], prior_sd=1000)
    assert wide.ratings[0] > fitted.ratings[0]


def test_a_tie_counts_half_a_win_for_each() -> None:
    fitted = fit_ratings(2, [0, 1], [1, 0], [0.5, 0.5], prior_sd=200)
    assert np.allclose(fitted.ratings, 0)
    by_halves = fit_ratings(2, [0, 0], [1, 1], [1.0, 0.0], prior_sd=200)
    assert np.allclose(by_halves.ratings, 0)


def test_a_player_who_never_played_keeps_the_prior() -> None:
    fitted = fit_ratings(3, [0, 0], [1, 1], [1.0, 1.0], prior_sd=200)
    assert fitted.ratings[2] == pytest.approx(0)
    assert fitted.ratings.sum() == pytest.approx(0)
    assert fitted.standard_errors[2] > max(fitted.standard_errors[:2])


def test_a_player_who_always_wins_has_a_finite_rating() -> None:
    fitted = fit_ratings(2, [0] * 50, [1] * 50, [1.0] * 50, prior_sd=300)
    assert np.all(np.isfinite(fitted.ratings))
    assert fitted.ratings[0] > 300
