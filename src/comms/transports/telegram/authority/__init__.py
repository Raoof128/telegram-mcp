"""Authority package: policy engine, opaque refs, cursor binding, epochs.

Import the submodule you need. The package re-exported the policy engine until comms
v0.3; it no longer does, because importing any ``authority.*`` module would then make the
retired evaluator reachable from production (A3).
"""
