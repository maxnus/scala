"""Which engine plays a machine's matches: AI Arena's by default, until Scala's own has played bots of every type, as
step 8c of `docs/plan.md` says, or Scala's, by `engine` in `runner.toml` or `sc2scala play --engine`."""

from sc2scala.aiarena_engine import AiArenaEngine
from sc2scala.scala_engine import ScalaEngine

AIARENA_ENGINE = AiArenaEngine.name
SCALA_ENGINE = ScalaEngine.name
ENGINE_NAMES = (AIARENA_ENGINE, SCALA_ENGINE)
DEFAULT_ENGINE = AIARENA_ENGINE
ENGINE_TITLES = {AIARENA_ENGINE: "AI Arena's engine", SCALA_ENGINE: "Scala's engine"}
ENGINES_PLAYING_COMPUTERS = frozenset({SCALA_ENGINE})
"""The engines that seat the game's built-in computer, for which alone the server draws a match with one."""


def engine_named(name: str) -> AiArenaEngine | ScalaEngine:
    """The engine `name` names, one of `ENGINE_NAMES`; raises `ValueError` for any other."""
    if name == AIARENA_ENGINE:
        return AiArenaEngine()
    if name == SCALA_ENGINE:
        return ScalaEngine()
    raise ValueError(f"{name!r} is no engine: one of {', '.join(ENGINE_NAMES)}")


def images_of_every_engine() -> list[str]:
    """The Docker images of every engine, each once, in the order the engines name them."""
    images = [image for name in ENGINE_NAMES for image in engine_named(name).images]
    return list(dict.fromkeys(images))
