"""Testes unitários de scoring.py — rode com: python -m unittest discover tests"""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scoring  # noqa: E402


class TestPoisson(unittest.TestCase):
    def test_pmf_zero(self):
        self.assertAlmostEqual(scoring.poisson_pmf(0, 1.0), math.exp(-1))

    def test_pmf_known_value(self):
        # P(X=2 | lam=3) = e^-3 * 9/2
        self.assertAlmostEqual(scoring.poisson_pmf(2, 3.0),
                               math.exp(-3) * 9 / 2)

    def test_pmf_negative_k(self):
        self.assertEqual(scoring.poisson_pmf(-1, 2.0), 0.0)

    def test_pmf_negative_lambda(self):
        with self.assertRaises(ValueError):
            scoring.poisson_pmf(1, -0.5)

    def test_cdf_sums_pmf(self):
        lam = 4.7
        expected = sum(scoring.poisson_pmf(i, lam) for i in range(6))
        self.assertAlmostEqual(scoring.poisson_cdf(5, lam), expected)

    def test_over_under_half_line_complementary(self):
        lam, line = 4.8, 4.5
        self.assertAlmostEqual(
            scoring.prob_over(lam, line) + scoring.prob_under(lam, line), 1.0)

    def test_integer_line_includes_push(self):
        lam, line = 4.8, 5.0
        total = (scoring.prob_over(lam, line) + scoring.prob_under(lam, line)
                 + scoring.prob_push(lam, line))
        self.assertAlmostEqual(total, 1.0)
        self.assertAlmostEqual(scoring.prob_push(lam, line),
                               scoring.poisson_pmf(5, lam))

    def test_prob_over_matches_manual(self):
        # P(X > 2.5 | lam=2) = 1 - P(0)-P(1)-P(2)
        lam = 2.0
        manual = 1 - sum(scoring.poisson_pmf(k, lam) for k in range(3))
        self.assertAlmostEqual(scoring.prob_over(lam, 2.5), manual)


class TestMetrics(unittest.TestCase):
    def test_implied_prob(self):
        self.assertAlmostEqual(scoring.implied_prob(2.0), 0.5)

    def test_implied_prob_invalid_odd(self):
        with self.assertRaises(ValueError):
            scoring.implied_prob(1.0)

    def test_edge(self):
        self.assertAlmostEqual(scoring.edge(0.55, 2.0), 0.05)

    def test_ev_positive(self):
        # p=0.55, odd=2.0 => EV = 0.55*1 - 0.45 = 0.10
        self.assertAlmostEqual(scoring.ev_per_unit(0.55, 2.0), 0.10)

    def test_ev_zero_at_fair_odd(self):
        # odd justa = 1/p => EV = 0
        p = 0.4
        self.assertAlmostEqual(scoring.ev_per_unit(p, 1 / p), 0.0)

    def test_breakeven(self):
        self.assertAlmostEqual(scoring.breakeven_rate(1.25), 0.8)

    def test_wins_to_recover(self):
        # odd 1.25: 1/(0.25) = 4 vitórias para recuperar 1 derrota
        self.assertAlmostEqual(scoring.wins_to_recover(1.25), 4.0)
        self.assertAlmostEqual(scoring.wins_to_recover(2.0), 1.0)

    def test_wins_to_recover_invalid(self):
        with self.assertRaises(ValueError):
            scoring.wins_to_recover(1.0)

    def test_market_metrics_full(self):
        m = scoring.market_metrics(0.6, 2.0)
        self.assertAlmostEqual(m.implied, 0.5)
        self.assertAlmostEqual(m.edge, 0.1)
        self.assertAlmostEqual(m.ev, 0.2)
        self.assertAlmostEqual(m.wins_to_recover, 1.0)

    def test_market_metrics_without_odd(self):
        m = scoring.market_metrics(0.6, None)
        self.assertIsNone(m.edge)
        self.assertTrue(m.notes)


class TestOutcome(unittest.TestCase):
    def test_probs_sum_to_one(self):
        p = scoring.match_outcome_probs(1.4, 1.1)
        self.assertAlmostEqual(sum(p), 1.0)

    def test_symmetric_lambdas(self):
        ph, pd, pa = scoring.match_outcome_probs(1.3, 1.3)
        self.assertAlmostEqual(ph, pa, places=9)

    def test_stronger_home_team(self):
        ph, _, pa = scoring.match_outcome_probs(2.5, 0.8)
        self.assertGreater(ph, pa)


class TestCombine(unittest.TestCase):
    def test_three_legs(self):
        t = scoring.combine_legs([(1.5, 0.7), (2.0, 0.5), (1.8, 0.6)])
        self.assertAlmostEqual(t.combined_odd, 1.5 * 2.0 * 1.8)
        self.assertAlmostEqual(t.combined_prob, 0.7 * 0.5 * 0.6)
        self.assertAlmostEqual(t.lose_prob, 1 - 0.21)
        self.assertIn("INDEPENDÊNCIA", t.warning)

    def test_empty(self):
        with self.assertRaises(ValueError):
            scoring.combine_legs([])

    def test_invalid_leg(self):
        with self.assertRaises(ValueError):
            scoring.combine_legs([(1.0, 0.5)])
        with self.assertRaises(ValueError):
            scoring.combine_legs([(1.5, 1.2)])


if __name__ == "__main__":
    unittest.main()


class TestShrinkage(unittest.TestCase):
    """Regressão à média: poucos jogos com média extrema são puxados ao baseline."""

    def setUp(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent))

    def test_shrink_pulls_toward_baseline(self):
        import recommender
        # 1 jogo só, valor extremo -> bem perto do baseline
        v = recommender._shrink(4.0, games=1, baseline=1.3)
        self.assertLess(v, 4.0)
        self.assertGreater(v, 1.3)

    def test_shrink_more_games_closer_to_observed(self):
        import recommender
        few = recommender._shrink(4.0, games=2, baseline=1.3)
        many = recommender._shrink(4.0, games=30, baseline=1.3)
        self.assertGreater(many, few)  # mais jogos = mais perto do observado

    def test_shrink_disabled_with_k_zero(self):
        import config, recommender
        old = config.MODEL["shrink_k"]
        config.MODEL["shrink_k"] = 0
        try:
            self.assertEqual(recommender._shrink(4.0, 3, 1.3), 4.0)
        finally:
            config.MODEL["shrink_k"] = old


class TestDixonColes(unittest.TestCase):
    """Correção de Dixon-Coles: sobe placares baixos/empate; rho=0 = Poisson."""

    def test_raises_draw_vs_independent(self):
        import config
        lh, la = 1.4, 1.2
        config.MODEL["dixon_coles_rho"] = 0.0
        _, pd0, _ = scoring.match_outcome_probs(lh, la)
        config.MODEL["dixon_coles_rho"] = -0.10
        _, pd1, _ = scoring.match_outcome_probs(lh, la)
        config.MODEL["dixon_coles_rho"] = -0.10
        self.assertGreater(pd1, pd0)

    def test_rho_zero_is_independent_poisson(self):
        import config
        old = config.MODEL["dixon_coles_rho"]
        config.MODEL["dixon_coles_rho"] = 0.0
        try:
            m = scoring.score_matrix(1.3, 1.1)
            tot = sum(scoring.poisson_pmf(h, 1.3) * scoring.poisson_pmf(a, 1.1)
                      for h in range(11) for a in range(11))
            self.assertAlmostEqual(
                m[2][1], scoring.poisson_pmf(2, 1.3) *
                scoring.poisson_pmf(1, 1.1) / tot)
        finally:
            config.MODEL["dixon_coles_rho"] = old

    def test_matrix_sums_to_one(self):
        m = scoring.score_matrix(1.7, 1.0)
        self.assertAlmostEqual(sum(sum(r) for r in m), 1.0)
