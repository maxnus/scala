"""Which engine plays a machine's matches: AI Arena's by default, until Scala's own has played bots of every type, as
step 8c of `docs/plan.md` says, or Scala's, by `engine` in `runner.toml` or `sc2scala play --engine`."""

from sc2scala.aiarena_engine import AiArenaEngine
from sc2scala.engine_names import AIARENA_ENGINE, ENGINE_NAMES, SCALA_ENGINE
from sc2scala.scala_engine import ScalaEngine

DEFAULT_ENGINE = AIARENA_ENGINE


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
