"""Character voices: text-to-speech models that NPC Forge runs in their own Python environments (see runtime.py).

Nothing in this package imports torch: the models run in worker processes (worker.py) started from each engine's own
environment, so NPC Forge itself keeps needing only Pillow and numpy.
"""
