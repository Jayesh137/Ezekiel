"""Observation dependence and assertion scope; counts are never probabilities."""

CATEGORIES = ('protocol', 'financial', 'behaviour', 'successor')
VECTOR_CATEGORY = {'transfer': 'financial', 'hl_native': 'financial', 'linkage': 'financial',
                   'correlation': 'financial', 'shared_agent': 'protocol', 'explicit_link': 'protocol',
                   'behavioural': 'behaviour', 'dormancy_handoff': 'successor', 'referral': 'protocol'}


def aggregate_evidence(observations: list[dict]) -> dict:
    rows = [row for row in observations if isinstance(row, dict) and row.get('status', 'ok') == 'ok']
    roots = list(range(len(rows)))

    def root(i):
        while roots[i] != i:
            roots[i] = roots[roots[i]]
            i = roots[i]
        return i

    categories = [row.get('category', VECTOR_CATEGORY.get(row.get('source'), 'successor')) for row in rows]
    legacy = {categories[i] for i, row in enumerate(rows) if not row.get('parent_event_ids')}
    owners = {}
    for i, row in enumerate(rows):
        parents = [str(p) for p in row.get('parent_event_ids', []) if p]
        # If one producer omitted provenance, independence within its category
        # is unknown even when another producer supplied exact identifiers.
        if categories[i] in legacy:
            parents.append('unresolved-dependence:' + categories[i])
        for parent in parents:
            if parent in owners:
                roots[root(i)] = root(owners[parent])
            owners[parent] = i
    groups = {}
    for i, _row in enumerate(rows):
        groups.setdefault(root(i), []).append(i)
    families = []
    for indexes in groups.values():
        cats = sorted({categories[i] for i in indexes})
        families.append({'sources': sorted({str(rows[i].get('source', 'unknown')) for i in indexes}),
                         'categories': cats,
                         'parent_event_ids': sorted({str(p) for i in indexes for p in rows[i].get('parent_event_ids', []) if p}),
                         'provenance_complete': all(rows[i].get('parent_event_ids') for i in indexes),
                         'research_only': all(rows[i].get('research_only') or categories[i] in ('behaviour', 'successor')
                                              for i in indexes)})
    return {'families': families, 'independent_groups': len(families),
            'promotable_groups': sum(not f['research_only'] for f in families),
            'categories': {c: [i for i, f in enumerate(families) if c in f['categories']] for c in CATEGORIES},
            'legacy_observations': sum(not row.get('parent_event_ids') for row in rows),
            'identity_confirmed': False, 'count_kind': 'observation_groups_not_owners',
            'caveat': 'Distinct events may still share an unobserved cause; shared authority is not beneficial ownership.'}


def vector_observations(vectors):
    return [{'source': vector, 'category': VECTOR_CATEGORY.get(vector, 'successor')} for vector in sorted(vectors)]
