"""Make ``chat/`` importable as a Python package.

Adds ``chat`` as a top-level package so that ``from chat.evals import ...``
resolves correctly when the parent directory is on ``sys.path``
(e.g. when running ``pytest`` from the repo root, or when invoking
``python -c`` with ``PYTHONPATH`` set to the parent). The other chat
subpackages (``chat_server``, ``chat_tests``) keep their existing
flat-package import surface (``from chat_server import pipeline``)
because they're discovered as subpackages of ``chat``.
"""

__all__: list[str] = []
