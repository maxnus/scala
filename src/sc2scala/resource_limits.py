from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceLimits:
    """What the containers of a match may use.

    Each bot gets `bot_cpus` cores and `bot_memory_mib` of memory, with no swap, and may run `bot_processes` processes
    and threads; a bot that needs more memory is killed, which loses it the match. Its `/tmp` and `data` folders are
    held in memory, and count against it. A bot whose logs grow past `bot_log_mib` is stopped and loses the match.
    The game's two clients share `game_cpus` cores and `game_memory_mib` of memory.
    """

    bot_cpus: float = 2.0
    bot_memory_mib: int = 4096
    bot_processes: int = 1024
    bot_log_mib: int = 100
    game_cpus: float = 4.0
    game_memory_mib: int = 6144
