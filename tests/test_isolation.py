"""扫码后端 backends/web/ 必须能整体删除，而不影响其他模块导入和运行。"""

import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "tunetidy"

CHECK = """
import importlib, pkgutil, sys
import tunetidy
from tunetidy.backends import UnknownBackendError, available_backends, get_backend
from tunetidy.config import Config

assert tunetidy.__file__.startswith(sys.argv[1]), tunetidy.__file__
names = [m.name for m in pkgutil.walk_packages(tunetidy.__path__, "tunetidy.")]
assert not any(n.startswith("tunetidy.backends.web") for n in names), names
for n in names:
    importlib.import_module(n)
assert "web" not in available_backends()
try:
    get_backend("web", Config())
except UnknownBackendError as e:
    assert "已被移除" in str(e)
else:
    raise AssertionError("expected UnknownBackendError")
print("ok", len(names))
"""


def test_core_works_without_web_backend(tmp_path: Path) -> None:
    pkg = tmp_path / "tunetidy"
    shutil.copytree(SRC, pkg, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.rmtree(pkg / "backends" / "web")
    out = subprocess.run(
        [sys.executable, "-c", CHECK, str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith("ok")
