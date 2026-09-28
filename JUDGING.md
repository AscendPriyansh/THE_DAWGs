# Judging & Normalisation Specification

## 1. Overview & Objective

The DOGFOOD platform implements a dual-method evaluation system:
1. `RAW_WEIGHTED_V1`: Standard rubric-weighted average per project.
2. `RIDGE_JUDGE_OFFSET_V1`: Regularized additive judge-bias adjustment model that calibrates project quality against judge severity/leniency.

All scoring algorithms are implemented as pure, deterministic functions in [`apps/judging/calculation.py`](file:///home/mayanksoni/Downloads/dogfood/src/backend/apps/judging/calculation.py). They operate on immutable database snapshots and produce reproducible outputs verified by SHA-256 digests.

---

## 2. Review Scoring & Weights

For each completed review by judge $j$ on project $p$, the weighted review score $x_{j,p}$ is computed as:

$$x_{j,p} = \frac{\sum_{c \in C} w_c \cdot s_{j,p,c}}{\sum_{c \in C} w_c}$$

Where:
- $C$ is the set of criteria in the frozen rubric (e.g. Functionality, Quality, Innovation).
- $w_c > 0$ is the configured positive weight of criterion $c$.
- $s_{j,p,c} \in \{1, 2, 3, 4, 5\}$ is the integer score assigned by judge $j$.

Every completed review has equal weight within the project; criterion weights are applied inside that review. Partial drafts are permitted during evaluation, but official submission requires complete scores for all criteria.

---

## 3. Ridge Judge Offset Model (`RIDGE_JUDGE_OFFSET_V1`)

### Mathematical Formulation

In competitive hackathons, some judges are systematically harsher or more generous than others. The ridge offset model decomposes each observed score into a latent project quality $q_p$ and an additive judge bias $b_j$:

$$\min_{q, b} \sum_{(j, p) \in \Omega} (x_{j,p} - q_p - b_j)^2 + \lambda \sum_{j \in J} b_j^2$$

Where:
- $\Omega$ is the set of all observed judge-project review pairs.
- $\lambda > 0$ is the ridge regularization penalty (default: $\lambda = 5.0$, frozen before judging).
- $q_p$ represents the estimated true quality of project $p$.
- $b_j$ represents the estimated generosity ($b_j > 0$) or severity ($b_j < 0$) of judge $j$.

### Regularization Behavior & Shrinkage

The penalty term $\lambda \sum b_j^2$ serves two critical functions:
1. **Shrinks Weak Evidence to Zero**: A judge who reviews only 1 or 2 projects has insufficient overlap to establish reliable bias; $\lambda$ pulls their offset $b_j$ close to $0$.
2. **Guarantees Uniqueness & Strict Convexity**: Because the objective function is strictly convex when $\lambda > 0$, the global minimum is unique, eliminating arbitrary constant shift degeneracies.
3. **No Division by Zero for Constant Judges**: Unlike variance-normalisation methods (such as Z-scores) which divide by judge variance $\sigma_j$ and break down when a judge gives all identical scores (e.g., all 4s), the Ridge Judge Offset model only divides by $(|P_j| + \lambda)$, which is strictly positive for all judges.

---

## 4. Deterministic Solver

The solver executes coordinate descent with exact deterministic ordering:

1. **Deterministic Ordering**: Sort all project IDs and judge IDs lexicographically.
2. **Initialization**:
   - Set all judge offsets $b_j^{(0)} = 0$.
   - Set all project qualities $q_p^{(0)} = \text{raw\_mean}(p)$.
3. **Coordinate Descent Updates**:
   - For each project $p$:
     $$q_p^{(t+1)} = \frac{1}{|J_p|} \sum_{j \in J_p} (x_{j,p} - b_j^{(t)})$$
   - For each judge $j$:
     $$b_j^{(t+1)} = \frac{\sum_{p \in P_j} (x_{j,p} - q_p^{(t+1)})}{|P_j| + \lambda}$$
4. **Convergence Check**:
   $$\max \left( \max_p |q_p^{(t+1)} - q_p^{(t)}|, \max_j |b_j^{(t+1)} - b_j^{(t)}| \right) < 10^{-10}$$
   If convergence is reached, terminate. Maximum iterations: $10{,}000$.

---

## 5. Ranking & Tie-Break Rules

1. **Competition Ranking**: Ranks are assigned using competition standard numbering (1, 1, 3).
2. **Precision**: Ranking scores are rounded to 6 decimal places for tie comparison.
3. **Display vs Storage**: Adjusted values can fall outside the discrete 1–5 scale (e.g. 4.8251 or 0.95); they are preserved as continuous indices and never clipped.
4. **No Invention**: Projects with zero reviews receive `rank = None` and flag `NO_REVIEWS`. Projects with fewer reviews than `required_reviews` receive flag `FEWER_THAN_REQUIRED_REVIEWS`.

---

## 6. Graph Connectivity Diagnostics

The review assignments form a bipartite graph between projects and judges. The platform analyzes connected components using breadth-first search:
- **Single Component**: Full cross-calibration exists across the cohort.
- **Multiple Components (`DISCONNECTED_COMPARISON_GROUPS`)**: Groups of projects were evaluated by disjoint sets of judges. The platform flags this as a waivable warning that must be acknowledged prior to official publication.
