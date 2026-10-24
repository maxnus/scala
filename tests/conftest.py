import os
from pathlib import Path

import pytest


@pytest.fixture
def map_file() -> Path:
    """The map named by `SC2SCALA_TEST_MAP`, a path to an `.SC2Map` file, for the tests that play a match."""
    test_map = os.environ.get("SC2SCALA_TEST_MAP")
    if not test_map:
        pytest.skip("SC2SCALA_TEST_MAP names no map")
    return Path(test_map)
