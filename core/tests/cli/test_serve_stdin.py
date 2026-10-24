import os
import subprocess
import sys
import time


def test_serve_exits_when_stdin_closes(tmp_path):
    home = tmp_path / "serve-home"
    env = {**os.environ, "MISES_HOME": str(home)}
    code = "import sys; from mises.cli.main import main; sys.exit(main(['serve', '--exit-on-stdin-eof']))"
    p = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    try:
        for _ in range(150):
            if (home / "server.json").exists():
                break
            time.sleep(0.1)
        assert (home / "server.json").exists()
        p.stdin.close()
        assert p.wait(timeout=5) == 0
        assert not (home / "server.json").exists()
    finally:
        if p.poll() is None:
            p.kill()
