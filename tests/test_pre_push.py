"""pre-push 钩子：只允许推送 main 分支和 v* 发版标签；其他分支和标签只留在本机。"""

import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "scripts" / "hooks" / "pre-push"
SHA = "1" * 40


def push(*remote_refs: str) -> subprocess.CompletedProcess[str]:
    lines = "".join(f"refs/x {SHA} {r} {'0' * 40}\n" for r in remote_refs)
    return subprocess.run(
        ["sh", str(HOOK), "origin", "url"], input=lines, capture_output=True, text=True
    )


@pytest.mark.parametrize("ref", ["refs/heads/main", "refs/tags/v1.0.0"])
def test_allows_main_and_release_tags(ref: str) -> None:
    assert push(ref).returncode == 0


@pytest.mark.parametrize("ref", ["refs/heads/archive", "refs/tags/internal", "refs/heads/feature"])
def test_blocks_everything_else(ref: str) -> None:
    r = push("refs/heads/main", ref)
    assert r.returncode == 1 and ref in r.stderr
