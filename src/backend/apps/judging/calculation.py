import hashlib
import json
from collections import defaultdict, deque
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple


def canonical_json_hash(data: Any) -> str:
    """Computes SHA-256 hash of data serialized to canonical sorted JSON."""
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_weighted_review_score(
    scores: Dict[str, int], criteria_weights: Dict[str, Decimal]
) -> float:
    """
    Computes weighted score for a review:
    x[j, p] = sum(weight[c] * score[j, p, c]) / sum(weight[c])
    """
    total_weight = Decimal("0.0")
    weighted_sum = Decimal("0.0")

    for crit_key, weight in criteria_weights.items():
        if crit_key in scores:
            val = Decimal(str(scores[crit_key]))
            weighted_sum += weight * val
            total_weight += weight

    if total_weight == Decimal("0.0"):
        return 0.0
    return float(weighted_sum / total_weight)


def find_connected_components(
    project_ids: List[str], judge_ids: List[str], reviews: List[Dict[str, Any]]
) -> Dict[str, int]:
    """
    Finds connected components in the bipartite project-judge review graph.
    Returns mapping from project_id to component index (1-based).
    """
    adj: Dict[str, Set[str]] = defaultdict(set)
    for r in reviews:
        p_node = f"p:{r['project_id']}"
        j_node = f"j:{r['judge_id']}"
        adj[p_node].add(j_node)
        adj[j_node].add(p_node)

    visited: Set[str] = set()
    component_map: Dict[str, int] = {}
    current_component = 0

    sorted_p_nodes = [f"p:{pid}" for pid in sorted(project_ids)]

    for p_node in sorted_p_nodes:
        if p_node not in visited and p_node in adj:
            current_component += 1
            queue = deque([p_node])
            visited.add(p_node)

            while queue:
                curr = queue.popleft()
                if curr.startswith("p:"):
                    component_map[curr[2:]] = current_component

                for neighbor in sorted(adj[curr]):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)

    # For isolated projects with no reviews
    for pid in project_ids:
        if pid not in component_map:
            current_component += 1
            component_map[pid] = current_component

    return component_map


def solve_ridge_judge_offsets(
    project_ids: List[str],
    judge_ids: List[str],
    reviews: List[Dict[str, Any]],
    lambda_val: float = 5.0,
    tol: float = 1e-10,
    max_iter: int = 10000,
) -> Tuple[Dict[str, float], Dict[str, float], Dict[str, Any]]:
    """
    Deterministic solver for Ridge Judge Offset model:
    Minimizes: sum_{(j,p)} (x[j,p] - q[p] - b[j])^2 + lambda * sum_j b[j]^2

    Returns: (project_values q, judge_offsets b, diagnostics)
    """
    # 1. Stable sorted IDs
    sorted_p = sorted(project_ids)
    sorted_j = sorted(judge_ids)

    # Build review lookup
    # p -> list of (judge_id, x_val)
    reviews_by_p: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
    # j -> list of (project_id, x_val)
    reviews_by_j: Dict[str, List[Tuple[str, float]]] = defaultdict(list)

    for r in reviews:
        pid = r["project_id"]
        jid = r["judge_id"]
        x_val = r["weighted_score"]
        reviews_by_p[pid].append((jid, x_val))
        reviews_by_j[jid].append((pid, x_val))

    # Initialize b[j] = 0
    b: Dict[str, float] = {jid: 0.0 for jid in sorted_j}

    # Initialize q[p] to raw mean
    q: Dict[str, float] = {}
    for pid in sorted_p:
        revs = reviews_by_p[pid]
        if revs:
            q[pid] = sum(x for _, x in revs) / len(revs)
        else:
            q[pid] = 0.0

    # Iterative coordinate descent
    iterations = 0
    converged = False
    max_delta = 0.0

    for it in range(1, max_iter + 1):
        iterations = it
        current_max_delta = 0.0

        # Update q[p]
        for pid in sorted_p:
            revs = reviews_by_p[pid]
            if not revs:
                continue
            new_q = sum(x - b[jid] for jid, x in revs) / len(revs)
            diff = abs(new_q - q[pid])
            if diff > current_max_delta:
                current_max_delta = diff
            q[pid] = new_q

        # Update b[j]
        for jid in sorted_j:
            revs = reviews_by_j[jid]
            if not revs:
                b[jid] = 0.0
                continue
            numerator = sum(x - q[pid] for pid, x in revs)
            new_b = numerator / (len(revs) + lambda_val)
            diff = abs(new_b - b[jid])
            if diff > current_max_delta:
                current_max_delta = diff
            b[jid] = new_b

        max_delta = current_max_delta
        if current_max_delta < tol:
            converged = True
            break

    # Compute objective and residual sum of squares
    rss = 0.0
    for r in reviews:
        pred = q[r["project_id"]] + b[r["judge_id"]]
        rss += (r["weighted_score"] - pred) ** 2

    ridge_penalty = lambda_val * sum(val ** 2 for val in b.values())
    total_objective = rss + ridge_penalty

    diagnostics = {
        "iterations": iterations,
        "converged": converged,
        "final_delta": max_delta,
        "rss": round(rss, 8),
        "ridge_penalty": round(ridge_penalty, 8),
        "objective": round(total_objective, 8),
        "lambda": lambda_val,
        "judges_count": len(sorted_j),
        "projects_count": len(sorted_p),
        "reviews_count": len(reviews),
    }

    return q, b, diagnostics


def assign_competition_ranks(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Assigns competition ranks (1, 1, 3) to eligible rows with valid ranking values.
    Ties are determined by rounding ranking_value to 6 decimal places.
    """
    ranked_eligible = [r for r in rows if r["eligible"] and r["ranking_value"] is not None]
    unranked = [r for r in rows if not (r["eligible"] and r["ranking_value"] is not None)]

    # Sort descending by ranking value (6 decimal places), then stable tie-break on project_id for deterministic order
    ranked_eligible.sort(
        key=lambda r: (-round(r["ranking_value"], 6), r["project_id"])
    )

    current_rank = 1
    for i, r in enumerate(ranked_eligible):
        if i > 0:
            prev = ranked_eligible[i - 1]
            if round(r["ranking_value"], 6) == round(prev["ranking_value"], 6):
                r["rank"] = prev["rank"]
            else:
                r["rank"] = i + 1
        else:
            r["rank"] = 1

    for r in unranked:
        r["rank"] = None

    return ranked_eligible + unranked


def execute_scoring_run(
    input_snapshot: Dict[str, Any],
    algorithm: str = "RIDGE_JUDGE_OFFSET_V1",
    lambda_val: float = 5.0,
    required_reviews: int = 3,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Pure deterministic scoring execution taking input snapshot and returning:
    (result_rows, run_diagnostics)
    """
    projects = input_snapshot.get("projects", [])
    criteria = input_snapshot.get("criteria", [])
    raw_reviews = input_snapshot.get("reviews", [])

    criteria_weights = {c["key"]: Decimal(str(c["weight"])) for c in criteria}

    # Compute review weighted score
    processed_reviews = []
    reviews_by_project = defaultdict(list)
    judges_set = set()

    for rev in raw_reviews:
        score_val = compute_weighted_review_score(rev["scores"], criteria_weights)
        item = {
            "review_id": rev["review_id"],
            "project_id": rev["project_id"],
            "judge_id": rev["judge_id"],
            "weighted_score": score_val,
        }
        processed_reviews.append(item)
        reviews_by_project[rev["project_id"]].append(score_val)
        judges_set.add(rev["judge_id"])

    project_ids = [p["id"] for p in projects]
    judge_ids = sorted(list(judges_set))

    # Graph components
    components = find_connected_components(project_ids, judge_ids, processed_reviews)
    unique_components = len(set(components.values()))

    # Raw mean per project
    raw_means = {}
    for pid in project_ids:
        scores = reviews_by_project[pid]
        raw_means[pid] = sum(scores) / len(scores) if scores else None

    adjusted_values: Dict[str, Optional[float]] = {}
    algo_diagnostics: Dict[str, Any] = {}

    if algorithm == "RIDGE_JUDGE_OFFSET_V1":
        # Fit ridge solver on projects with at least one review
        active_pids = [pid for pid in project_ids if pid in reviews_by_project]
        if active_pids:
            q_vals, b_vals, diag = solve_ridge_judge_offsets(
                active_pids, judge_ids, processed_reviews, lambda_val=lambda_val
            )
            adjusted_values = q_vals
            algo_diagnostics = diag
            algo_diagnostics["judge_offsets"] = {j: round(b, 6) for j, b in b_vals.items()}
        else:
            algo_diagnostics = {"status": "NO_REVIEWS_TO_FIT"}
    else:
        # RAW_WEIGHTED_V1
        adjusted_values = raw_means
        algo_diagnostics = {"algorithm": "RAW_WEIGHTED_V1", "status": "RAW_EVALUATION"}

    # Build result rows
    rows = []
    for p in projects:
        pid = p["id"]
        review_count = len(reviews_by_project.get(pid, []))
        raw_m = raw_means.get(pid)
        adj_val = adjusted_values.get(pid)

        flags = []
        eligible = p.get("eligible", True)
        exclusion_reason = p.get("exclusion_reason", "")

        if review_count == 0:
            flags.append("NO_REVIEWS")
            ranking_val = None
        else:
            if review_count < required_reviews:
                flags.append("FEWER_THAN_REQUIRED_REVIEWS")
            ranking_val = adj_val if eligible else None

        row = {
            "project_id": pid,
            "cohort_key": p.get("cohort_key", "EVENT"),
            "eligible": eligible,
            "exclusion_reason": exclusion_reason,
            "completed_review_count": review_count,
            "assigned_review_count": p.get("assigned_count", review_count),
            "raw_mean": round(raw_m, 6) if raw_m is not None else None,
            "adjusted_value": round(adj_val, 6) if adj_val is not None else None,
            "ranking_value": ranking_val,
            "comparison_component": components.get(pid, 1),
            "flags_json": flags,
        }
        rows.append(row)

    # Assign competition ranks
    ranked_rows = assign_competition_ranks(rows)

    # Compile run diagnostics
    run_diagnostics = {
        "algorithm": algorithm,
        "lambda": lambda_val,
        "total_projects": len(project_ids),
        "reviewed_projects": len([pid for pid in project_ids if pid in reviews_by_project]),
        "total_reviews": len(processed_reviews),
        "total_judges": len(judge_ids),
        "connected_components_count": unique_components,
        "has_disconnected_components": unique_components > 1,
        "algorithm_diagnostics": algo_diagnostics,
    }

    return ranked_rows, run_diagnostics
