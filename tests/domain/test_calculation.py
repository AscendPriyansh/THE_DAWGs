import pytest
from decimal import Decimal

from apps.judging.calculation import (
    assign_competition_ranks,
    compute_weighted_review_score,
    find_connected_components,
    solve_ridge_judge_offsets,
)


def test_compute_weighted_review_score():
    weights = {
        "functionality": Decimal("2.0"),
        "quality": Decimal("1.0"),
        "innovation": Decimal("1.0"),
    }
    # functionality=5 (wt 2 -> 10), quality=4 (wt 1 -> 4), innovation=2 (wt 1 -> 2)
    # total = 16 / 4 = 4.0
    scores = {"functionality": 5, "quality": 4, "innovation": 2}
    result = compute_weighted_review_score(scores, weights)
    assert abs(result - 4.0) < 1e-6


def test_solve_ridge_judge_offsets_known_system():
    """
    Test a controlled 2-project, 2-judge system where Judge 1 is systematically 1 point harsher
    than Judge 2.
    Reviews:
    (j1, p1) = 3.0, (j2, p1) = 4.0 -> p1 raw mean = 3.5
    (j1, p2) = 4.0, (j2, p2) = 5.0 -> p2 raw mean = 4.5
    True quality difference = 1.0 (p2 > p1).
    Judge 1 bias should be negative, Judge 2 bias should be positive, symmetric around 0.
    """
    project_ids = ["p1", "p2"]
    judge_ids = ["j1", "j2"]
    reviews = [
        {"project_id": "p1", "judge_id": "j1", "weighted_score": 3.0},
        {"project_id": "p1", "judge_id": "j2", "weighted_score": 4.0},
        {"project_id": "p2", "judge_id": "j1", "weighted_score": 4.0},
        {"project_id": "p2", "judge_id": "j2", "weighted_score": 5.0},
    ]

    q, b, diag = solve_ridge_judge_offsets(
        project_ids, judge_ids, reviews, lambda_val=5.0, tol=1e-10
    )

    assert diag["converged"] is True
    assert diag["iterations"] < 100

    # Symmetry checks
    assert abs(q["p2"] - q["p1"] - 1.0) < 1e-6
    assert b["j1"] < 0  # Harsh judge
    assert b["j2"] > 0  # Generous judge
    assert abs(b["j1"] + b["j2"]) < 1e-6  # Symmetric bias cancellation


def test_constant_judge_does_not_divide_by_zero():
    """
    A judge who gives the exact same score to all projects (variance = 0)
    must converge cleanly without any zero-division exception.
    """
    project_ids = ["p1", "p2", "p3"]
    judge_ids = ["j_const", "j_normal"]

    reviews = [
        # Constant judge gives 3.0 to everyone
        {"project_id": "p1", "judge_id": "j_const", "weighted_score": 3.0},
        {"project_id": "p2", "judge_id": "j_const", "weighted_score": 3.0},
        {"project_id": "p3", "judge_id": "j_const", "weighted_score": 3.0},
        # Normal judge gives varied scores
        {"project_id": "p1", "judge_id": "j_normal", "weighted_score": 2.0},
        {"project_id": "p2", "judge_id": "j_normal", "weighted_score": 3.5},
        {"project_id": "p3", "judge_id": "j_normal", "weighted_score": 5.0},
    ]

    q, b, diag = solve_ridge_judge_offsets(
        project_ids, judge_ids, reviews, lambda_val=5.0
    )

    assert diag["converged"] is True
    assert "j_const" in b
    assert isinstance(b["j_const"], float)


def test_single_review_judge_regularisation_shrinkage():
    """
    A judge with only 1 review has weak evidence; their offset b[j]
    should be shrunk heavily towards 0 by lambda=5.0.
    """
    project_ids = ["p1", "p2"]
    judge_ids = ["j_veteran", "j_one_off"]

    # j_veteran reviews both p1 and p2
    # j_one_off reviews only p1 and gives an extreme 5.0
    reviews = [
        {"project_id": "p1", "judge_id": "j_veteran", "weighted_score": 3.0},
        {"project_id": "p2", "judge_id": "j_veteran", "weighted_score": 3.0},
        {"project_id": "p1", "judge_id": "j_one_off", "weighted_score": 5.0},
    ]

    q, b, diag = solve_ridge_judge_offsets(
        project_ids, judge_ids, reviews, lambda_val=5.0
    )

    assert diag["converged"] is True
    # j_one_off offset must be substantially smaller than raw deviation (5.0 - 3.0 = 2.0)
    # because denominator is (1 + 5) = 6
    assert abs(b["j_one_off"]) < 0.5


def test_disconnected_components_graph_detection():
    """
    Two disjoint groups of projects and judges must be recognized as 2 components.
    """
    project_ids = ["p1", "p2", "p3", "p4"]
    judge_ids = ["j1", "j2"]

    # Component 1: {p1, p2, j1}
    # Component 2: {p3, p4, j2}
    reviews = [
        {"project_id": "p1", "judge_id": "j1", "weighted_score": 4.0},
        {"project_id": "p2", "judge_id": "j1", "weighted_score": 4.5},
        {"project_id": "p3", "judge_id": "j2", "weighted_score": 3.0},
        {"project_id": "p4", "judge_id": "j2", "weighted_score": 3.5},
    ]

    components = find_connected_components(project_ids, judge_ids, reviews)
    assert len(set(components.values())) == 2
    assert components["p1"] == components["p2"]
    assert components["p3"] == components["p4"]
    assert components["p1"] != components["p3"]


def test_competition_rankings_and_ties():
    """
    Verify competition ranking rules:
    Tied projects receive the same rank; subsequent project skips rank numbers (e.g. 1, 1, 3).
    """
    rows = [
        {"project_id": "p1", "ranking_value": 4.850000, "eligible": True},
        {"project_id": "p2", "ranking_value": 4.850000, "eligible": True},  # Tied with p1
        {"project_id": "p3", "ranking_value": 4.200000, "eligible": True},
        {"project_id": "p4", "ranking_value": None, "eligible": True},      # Unreviewed
        {"project_id": "p5", "ranking_value": 4.900000, "eligible": False}, # Ineligible
    ]

    ranked = assign_competition_ranks(rows)
    rank_by_id = {r["project_id"]: r["rank"] for r in ranked}

    assert rank_by_id["p1"] == 1
    assert rank_by_id["p2"] == 1
    assert rank_by_id["p3"] == 3
    assert rank_by_id["p4"] is None
    assert rank_by_id["p5"] is None
