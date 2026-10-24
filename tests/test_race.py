import pytest

from sc2scala import Race


@pytest.mark.parametrize(
    ("text", "race"), [("T", Race.TERRAN), ("z", Race.ZERG), ("Protoss", Race.PROTOSS), ("random", Race.RANDOM)]
)
def test_a_race_is_named_by_its_letter_or_its_name(text: str, race: Race) -> None:
    assert Race.from_text(text) == race


@pytest.mark.parametrize("text", ["X", "", "Terr"])
def test_any_other_text_is_no_race(text: str) -> None:
    with pytest.raises(ValueError, match="no race"):
        Race.from_text(text)
