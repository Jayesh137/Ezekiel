"""Bounded maximum-weight bipartite assignment, allowing unmatched observations."""

import math


def maximum_weight_pairs(edges: list[tuple], exact_limit: int = 64) -> tuple[list[tuple], str]:
    """Return distinct endpoint pairs; expose the fallback for large dense cohorts.

    The rectangular Hungarian algorithm includes a zero-weight dummy for each
    row. Missing edges can therefore never force a false match. Positive edge
    weights are heuristic utilities, not identity probabilities.
    """
    weights = {}
    for left, right, weight in edges:
        if isinstance(weight, (int, float)) and math.isfinite(weight) and weight > 0:
            weights[left, right] = max(weights.get((left, right), 0), weight)
    if not weights:
        return [], "exact"
    lefts = sorted({pair[0] for pair in weights})
    rights = sorted({pair[1] for pair in weights})
    transpose = len(lefts) > len(rights)
    if transpose:
        weights = {(right, left): weight for (left, right), weight in weights.items()}
        lefts, rights = rights, lefts
    if len(lefts) > exact_limit or len(lefts) * len(rights) > 131_072:
        used_left, used_right, pairs = set(), set(), []
        for (left, right), _weight in sorted(weights.items(), key=lambda item: (-item[1], item[0])):
            if left not in used_left and right not in used_right:
                used_left.add(left)
                used_right.add(right)
                pairs.append((right, left) if transpose else (left, right))
        return pairs, "bounded_greedy"

    n, m = len(lefts), len(rights) + len(lefts)
    u, v, p, way = [0.0] * (n + 1), [0.0] * (m + 1), [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minima, visited = [math.inf] * (m + 1), [False] * (m + 1)
        while True:
            visited[j0] = True
            i0, delta, j1 = p[j0], math.inf, 0
            for j in range(1, m + 1):
                if visited[j]:
                    continue
                weight = weights.get((lefts[i0 - 1], rights[j - 1]), 0) if j <= len(rights) else 0
                cost = -weight - u[i0] - v[j]
                if cost < minima[j]:
                    minima[j], way[j] = cost, j0
                if minima[j] < delta:
                    delta, j1 = minima[j], j
            for j in range(m + 1):
                if visited[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minima[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    pairs = []
    for j in range(1, len(rights) + 1):
        if p[j] and (lefts[p[j] - 1], rights[j - 1]) in weights:
            pair = lefts[p[j] - 1], rights[j - 1]
            pairs.append(pair[::-1] if transpose else pair)
    return sorted(pairs), "exact"
