import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend.rpa.engine_supervisor import build_spawn_command


def test_build_spawn_command_uses_repo_engine_entrypoint():
    command = build_spawn_command("D:/code/MyScienceClaw/RpaClaw/rpa-engine")

    assert "npm" in command[0].lower()
    assert "dev" in command[-1]
