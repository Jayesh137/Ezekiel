from src.evidence import aggregate_evidence


def test_transfer_and_native_report_of_same_event_are_one_family():
    result = aggregate_evidence([
        {'source': 'transfer', 'category': 'financial', 'parent_event_ids': ['tx1']},
        {'source': 'hl_native', 'category': 'financial', 'parent_event_ids': ['tx1']},
        {'source': 'correlation', 'category': 'financial', 'parent_event_ids': ['tx1', 'tx2']}])
    assert result['independent_groups'] == 1
    assert result['families'][0]['sources'] == ['correlation', 'hl_native', 'transfer']
    assert result['identity_confirmed'] is False


def test_transitive_dependencies_and_legacy_unknown_parents_are_conservative():
    result = aggregate_evidence([
        {'source': 'transfer', 'category': 'financial'},
        {'source': 'hl_native', 'category': 'financial'},
        {'source': 'route', 'category': 'financial', 'parent_event_ids': ['tx']},
        {'source': 'agent', 'category': 'protocol', 'parent_event_ids': ['approval']}])
    assert result['independent_groups'] == 2
    assert result['legacy_observations'] == 2
    assert result['categories']['protocol']


def test_cross_category_same_observation_is_not_independent_and_research_never_promotes():
    result = aggregate_evidence([
        {'source': 'timing', 'category': 'behaviour', 'parent_event_ids': ['fill1']},
        {'source': 'handoff', 'category': 'successor', 'parent_event_ids': ['fill1'], 'research_only': True}])
    assert result['independent_groups'] == 1
    assert result['promotable_groups'] == 0


def test_untrusted_roster_relationships_never_confirm_ownership():
    from src.roster import assign_tier
    for vectors in ({'shared_agent'}, {'explicit_link'}, {'transfer', 'hl_native'}, {'transfer', 'behavioural'}):
        assert assign_tier(vectors, .99, False, False) != 'CONFIRMED'
    assert assign_tier({'transfer', 'hl_native'}, .99, False, False) == 'POSSIBLE'
    assert assign_tier(set(), 0, False, True) == 'CONFIRMED'


def test_continuity_financial_descriptions_do_not_manufacture_independence():
    from src.continuity import score_continuity
    result = score_continuity({'direct_from_target': True, 'hl_native': True,
                               'two_way_flow': True, 'value_retained': 1})
    assert len(result['families']) == 1
    assert result['blockers']
