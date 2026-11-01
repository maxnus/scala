"""The engines' names, which a runner tells the server when it asks for a match, and which of them seat the game's
built-in computer: apart from the engines, so that the server knows them without importing one."""

AIARENA_ENGINE = "aiarena"
SCALA_ENGINE = "scala"
ENGINE_NAMES = (AIARENA_ENGINE, SCALA_ENGINE)
ENGINE_TITLES = {AIARENA_ENGINE: "AI Arena's engine", SCALA_ENGINE: "Scala's engine"}
ENGINES_PLAYING_COMPUTERS = frozenset({SCALA_ENGINE})
"""The engines that seat the game's built-in computer, for which alone the server draws a match with one."""
