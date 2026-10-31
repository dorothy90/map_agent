import pytest


def test_combines_exactly_one_character_bins_and_preserves_case():
    from symbol_bin import combine_bins

    assert combine_bins('B', 'c') == 'Bc'
    assert combine_bins('', 'B') is None
    assert combine_bins('AB', 'C') is None
    assert combine_bins('A', None) is None


def test_matches_left_right_or_either_patterns_as_a_union():
    from symbol_bin import compile_symbol_patterns, match_symbol_bins

    patterns = compile_symbol_patterns({
        'left': r'^B[^ ]$',
        'right': r'^[^ ]B$',
        'either': r'^(?:C.|.C)$',
        'same': r'^BB$',
    })
    assert match_symbol_bins('B', 'A', patterns)
    assert match_symbol_bins('A', 'B', patterns)
    assert match_symbol_bins('C', 'A', patterns)
    assert match_symbol_bins('B', 'B', patterns)
    assert not match_symbol_bins('b', 'a', patterns)
    assert not match_symbol_bins('A', 'A', patterns)


def test_invalid_regex_is_rejected_during_compile():
    from symbol_bin import compile_symbol_patterns

    with pytest.raises(ValueError, match='bad'):
        compile_symbol_patterns({'bad': '['})


def test_map_union_counts_each_coordinate_once_and_reports_unmatched_cells():
    from symbol_bin import compile_symbol_patterns, match_symbol_map

    result = match_symbol_map(
        {'MAP': ['0,0,B,B', '0,1,A,B', '1,0,AB,B', '1,1,A,A']},
        compile_symbol_patterns({'left': r'^B.$', 'right': r'^.B$'}),
    )
    assert result['fail_by_coordinate'] == {
        (0, 0): True,
        (0, 1): True,
        (1, 0): False,
        (1, 1): False,
    }
    assert result['fail_count'] == 2
    assert result['unmatched_cells'] == 1

