import { useEffect, useState } from "react";

import { post, type ApiError } from "../api";
import { navigate, to } from "../router";
import { askConfirm, Button, toneStyle } from "../ui";
import { useJob } from "./Progress";

const R = 88;
const C = 2 * Math.PI * R;

function minutesLeft(seconds: number): string {
  if (seconds < 60) return "不到 1 分钟";
  return `${Math.ceil(seconds / 60)} 分钟`;
}

/** 读取进度：圆环、预计剩余时间、已读到的曲风；读完自动进入整理结果。 */
export function Reading({ jobId }: { jobId: string }) {
  const job = useJob(jobId);
  const [problem, setProblem] = useState("");
  const [stopping, setStopping] = useState(false);

  useEffect(() => {
    if (job?.status === "done" && job.plan_id) navigate(to.result(job.plan_id), true);
  }, [job]);

  if (!job) return <div className="wrap small">正在读取进度…</div>;
  const source = (job.sources ?? []).map((s) => `「${s}」`).join(" + ");

  if (job.status === "failed" || job.status === "interrupted") {
    const cancelled = job.status === "failed" && job.error?.startsWith("已取消");
    return (
      <div className="center">
        <div className="wrap narrow">
          <h1>{cancelled ? "已取消读取" : job.status === "interrupted" ? "读取被中断了" : "读取没有完成"}</h1>
          <p className="sub">
            {job.status === "interrupted"
              ? "启动理音的窗口可能被关掉了。已读到的曲风有缓存，重新开始会快很多"
              : cancelled
                ? "已读到的曲风会保留，下次读取会跳过"
                : job.error}
          </p>
          <div className="mt-6">
            <Button primary onClick={() => navigate(to.newPlan())}>
              重新开始
            </Button>
          </div>
        </div>
      </div>
    );
  }

  const cached = job.cached ?? 0;
  const reading = job.phase === "获取曲风标签";
  const done = reading ? cached + job.done : 0;
  const total = reading ? cached + job.total : 0;
  const pct = total ? Math.floor((done / total) * 100) : 0;
  const eta = job.eta_seconds;

  const cancel = async () => {
    const ok = await askConfirm({
      title: "取消这次读取？",
      body: "已读到的曲风会保留，下次读取会跳过",
      ok: "取消读取",
      cancel: "继续读",
    });
    if (!ok) return;
    // 读完当前这首才会停下，停下后这一页会显示"已取消读取"
    post(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {})
      .then(() => setStopping(true))
      .catch((e: ApiError) => setProblem(e.message));
  };

  return (
    <div className="center">
      <div style={{ maxWidth: 560, width: "100%" }}>
        <p className="small">正在整理 · {source}</p>
        <div className="dial">
          <svg viewBox="0 0 200 200" aria-hidden>
            <circle cx="100" cy="100" r={R} fill="none" stroke="var(--soft2)" strokeWidth="12" />
            <circle
              cx="100"
              cy="100"
              r={R}
              fill="none"
              stroke="var(--fg)"
              strokeWidth="12"
              strokeLinecap="round"
              strokeDasharray={C}
              strokeDashoffset={C * (1 - pct / 100)}
              style={{ transition: "stroke-dashoffset 0.4s" }}
            />
          </svg>
          <div className="v">
            <div>
              <b>{pct}%</b>
              <div className="small">{reading ? `${done} / ${total} 首` : "正在读取歌单"}</div>
            </div>
          </div>
        </div>
        <h1 className="mt-5" style={{ fontSize: 28 }}>
          {eta != null ? `大约还要 ${minutesLeft(eta)}` : "正在读取歌单"}
        </h1>
        <p className="sub">为了不被网易云限制，读取会放慢一些，只有第一次需要这么久</p>
        {cached > 0 && <p className="small mt-1">其中 {cached} 首以前读过，不用再读</p>}
        <div className="genres">
          {(job.categories ?? []).map((c) => (
            <span key={c.name} className="pill" style={toneStyle(c.name, "其他")}>
              {c.name} {c.count}
            </span>
          ))}
          {(job.pending ?? 0) > 0 && <span className="pill p-gray">没分好 {job.pending}</span>}
          <span className="pill p-gray">还在读…</span>
        </div>
        <div className="banner text-left" style={{ marginTop: 30 }}>
          <div className="t">
            <b>可以先去忙别的</b>
            <div className="small">关掉网页不影响进度，只要启动理音的窗口还开着，回来就能接着看</div>
          </div>
        </div>
        {problem && <p className="mt-3 text-sm text-danger">{problem}</p>}
        <div className="mt-6 flex justify-center">
          <Button onClick={() => void cancel()} disabled={stopping}>
            {stopping ? "正在停下…" : "取消"}
          </Button>
        </div>
      </div>
    </div>
  );
}
