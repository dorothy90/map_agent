import re


WADS_BIN_CHAR_TO_PARAM = {
    "B": "BVDS",
    "C": "CONTACT",
    "D": "DIBL",
    "W": "EASY",
    "X": "FMAX",
    "G": "GATE_OX",
    "F": "IDDQ",
    "I": "IDSAT",
    "P": "IGATE",
    "O": "ION",
    "7": "ISB_CMOS",
    "J": "JUNCTION",
    "N": "LATCH",
    "L": "LEAK_ID",
    "R": "RON",
    "S": "SCAN",
    "H": "TPD",
    "T": "TWT",
    "M": "VMIN",
    "V": "VTH",
}


def test_bin_mapping_has_50_unique_characters_and_names():
    from bin_mapping import BIN_CHAR_TO_PARAM

    assert len(BIN_CHAR_TO_PARAM) == 50
    assert len(set(BIN_CHAR_TO_PARAM)) == 50
    assert len(set(BIN_CHAR_TO_PARAM.values())) == 50


def test_common_maps_are_derived_from_the_single_mapping_source():
    from bin_mapping import BIN_CHAR_TO_PARAM
    from common import BIN_CATEGORY_MAP, CATEGORY_TO_BIN

    assert BIN_CATEGORY_MAP == {"?": "PASS", **BIN_CHAR_TO_PARAM}
    assert CATEGORY_TO_BIN == {
        param_name: symbol for symbol, param_name in BIN_CHAR_TO_PARAM.items()
    }


def test_seed_rows_use_the_mapping_and_all_regexes_are_either():
    from bin_mapping import BIN_CHAR_TO_PARAM
    from seed_symbol_bin import SYMBOL_BIN_ROWS

    for _lot_cd, _process, param_nm, regex_pattern in SYMBOL_BIN_ROWS:
        match = re.fullmatch(r"(.+)\((.)\)", param_nm)
        assert match is not None
        param_name, symbol = match.groups()
        if param_nm != "DDA(d)":
            assert BIN_CHAR_TO_PARAM[symbol] == param_name
        assert regex_pattern == f"^({symbol}[^ ]|[^ ]{symbol})$"


def test_wads_names_match_the_integrated_mapping():
    from bin_mapping import BIN_CHAR_TO_PARAM

    assert {
        symbol: BIN_CHAR_TO_PARAM[symbol]
        for symbol in WADS_BIN_CHAR_TO_PARAM
    } == WADS_BIN_CHAR_TO_PARAM
