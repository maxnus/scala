"""Scala's match controller, which stands between two bots and the game while they play a match.

Scala's engine runs it in a container of the match, from a copy of this package beside copies of `websockets`,
`s2clientprotocol` and `protobuf`, so it imports nothing else of Scala's: only its own modules, by relative imports,
the standard library and those three.
"""
