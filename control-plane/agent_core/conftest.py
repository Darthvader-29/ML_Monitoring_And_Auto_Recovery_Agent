"""pytest path bootstrap — mirror agent.py so tests import the package the same way.

Adds the agent_core root (subpackages: monitoring/, detection/, ...) and _files/
(shared schemas/config) to sys.path.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
for _p in (str(_ROOT), str(_ROOT / "_files")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
