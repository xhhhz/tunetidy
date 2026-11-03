"""确认码不得出现在日志、异常信息、数据库和任何接口响应里。

跑一遍包含输错、过期、锁定、成功、失败任务、续传、撤销的完整流程，收集所有能看到的输出，
逐个检查显示通道里出现过的每一个码。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from tests.server.test_confirm_api import Env, confirm, submit, wrong
from tests.server.test_confirm_gate import FakeChannel
from tunetidy.plan import Plan
from tunetidy.storage import plans as plan_store
from tunetidy.storage.db import connect


def all_codes(channel: FakeChannel) -> set[str]:
    return {
        m for lines in channel.shown for m in re.findall(r"确认码[:：]\s*(\d{6})", "\n".join(lines))
    }


def test_codes_never_leak(
    env: Env, plan: Plan, db_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    c = connect(db_path)

    # 输错直到作废；过期后再申请；成功但写回失败；续传成功；最后撤销
    confirm(env, plan.id, plan.content_hash)
    for _ in range(3):
        submit(env, plan.id, wrong(env.code))
    confirm(env, plan.id, plan.content_hash)
    submit(env, plan.id, "12")  # 格式不对
    env.backend.fail_before["add_tracks"] = {3}
    confirm(env, plan.id, plan.content_hash)
    env.wait(submit(env, plan.id, env.code).json()["job_id"])
    env.backend.fail_before["add_tracks"] = set()
    current = plan_store.load_plan(c, plan.id)
    confirm(env, plan.id, current.content_hash)
    done = env.wait(submit(env, plan.id, env.code).json()["job_id"])
    assert done["status"] == "done"
    for path in (
        "/api/confirm",
        f"/api/plans/{plan.id}",
        f"/api/plans/{plan.id}/state",
        "/api/runs",
    ):
        env.call("GET", path)
    run_id = done["run_id"]
    preview = env.call("GET", f"/api/runs/{run_id}/undo").json()
    env.call(
        "POST", f"/api/runs/{run_id}/undo/confirm", json={"fingerprint": preview["fingerprint"]}
    )
    env.call(
        "POST",
        f"/api/runs/{run_id}/undo/submit",
        json={"code": wrong(env.code), "fingerprint": preview["fingerprint"]},
    )
    r = env.call(
        "POST",
        f"/api/runs/{run_id}/undo/submit",
        json={"code": env.code, "fingerprint": preview["fingerprint"]},
    )
    env.wait(r.json()["job_id"])
    c.close()

    codes = all_codes(env.channel)  # type: ignore[arg-type]
    assert len(codes) >= 5
    seen = "\n".join(env.responses)
    logs = "\n".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
    db_bytes = b"".join(p.read_bytes() for p in db_path.parent.glob(db_path.name + "*"))
    for code in codes:
        assert code not in seen, "确认码出现在接口响应里"
        assert code not in logs, "确认码出现在日志里"
        assert code.encode() not in db_bytes, "确认码出现在数据库文件里"
