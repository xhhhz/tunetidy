import { useCallback, useEffect, useState } from "react";

import { get, post, type ApiError, type JobSummary, type PlanDetail, type Ticket } from "../api";
import { useCodeFlow } from "../components/CodeDialog";
import { useLoad } from "../hooks";
import { navigate, to } from "../router";
import { Back, Cover, Icon, Link, toneStyle, coverLabel } from "../ui";

const POLL_MS = 1000;

/** 每秒读一次任务进度，直到结束。 */
export function useJob(jobId: string | null): JobSummary | null {
  const [job, setJob] = useState<JobSummary | null>(null);
  useEffect(() => {
    if (!jobId) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const j = await get<JobSummary>(`/api/jobs/${encodeURIComponent(jobId)}`);
        if (!live) return;
        setJob(j);
        if (j.status !== "running") return;
      } catch {
        // 连不上时由页面框架的断线提示处理，这里接着等
      }
      if (live) timer = setTimeout(() => void tick(), POLL_MS);
    };
    void tick();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [jobId]);
  return job;
}

const TAG: Record<string, string> = { 已完成: "p-ok", 添加中: "p-acc", 等待中: "p-gray" };

/** 正在建歌单 → 创建完成；中途断了显示"上次没建完"，可以继续。 */
export function Progress({ planId, jobId }: { planId: string; jobId: string }) {
  const job = useJob(jobId);
  const detail = useLoad<PlanDetail>(`/api/plans/${encodeURIComponent(planId)}`);
  const [problem, setProblem] = useState("");
  const onError = useCallback((e: ApiError) => setProblem(e.message), []);
  const flow = useCodeFlow({ title: "最后一步：确认是你本人", okLabel: "继续建", onError });
  const d = detail.data;
  const reloadDetail = detail.reload;
  useEffect(() => {
    if (job && job.status !== "running") void reloadDetail();
  }, [job, reloadDetail]);

  if (!job || !d) return <div className="wrap small">正在读取进度…</div>;
  const rows = job.playlists ?? [];
  const added = rows.reduce((n, r) => n + r.added, 0);
  const total = rows.reduce((n, r) => n + r.total, 0);
  const pct = total ? Math.round((added / total) * 100) : 0;

  const resume = () => {
    setProblem("");
    const id = encodeURIComponent(planId);
    void flow.start(
      () => post<Ticket>(`/api/plans/${id}/confirm`, { expected_hash: d.content_hash }),
      async (code) => {
        const r = await post<{ job_id: string }>(`/api/plans/${id}/confirm/submit`, { code });
        navigate(to.progress(planId, r.job_id), true);
      },
    );
  };

  if (job.status === "done") {
    return (
      <div className="wrap">
        <div className="text-center">
          <span className="mx-auto grid h-16 w-16 place-items-center rounded-full bg-ok-bg text-ok">
            <Icon name="check" size={32} />
          </span>
          <h1 className="mt-4">创建完成</h1>
          <p className="sub">
            打开网易云音乐 App，在「我的 · 创建的歌单」里就能看到，目前都是{d.private ? "私密" : "公开"}歌单
          </p>
          {(job.unaddable ?? 0) > 0 && (
            <p className="small mt-2">有 {job.unaddable} 首平台没有收下，已记为加不进去</p>
          )}
        </div>
        <div className="tiles">
          {d.playlists.map((p) => (
            <div key={p.category} className="tile">
              <Cover name={p.category} count={p.count} otherName={d.other_name} scale={0.9} />
              <div className="nm text-sm font-semibold">{p.name}</div>
            </div>
          ))}
        </div>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <button type="button" className="btn" onClick={() => navigate(to.home())}>
            完成
          </button>
          {job.run_id && (
            <button type="button" className="ghost" onClick={() => navigate(to.undo(job.run_id!))}>
              不满意，撤销
            </button>
          )}
        </div>
      </div>
    );
  }

  const stopped = job.status === "interrupted" || job.status === "failed";
  return (
    <div className="wrap">
      <Back to={to.result(planId)}>{d.sources.map((s) => s.name).join(" + ")}</Back>
      <h1>{stopped ? "上次没建完" : "正在建歌单"}</h1>
      <p className="sub">
        {stopped
          ? `建到 ${pct}% 时停下了${job.error ? `：${job.error}` : "，可能是电脑休眠或网络断开"}。继续时，已加入的歌不会重复添加`
          : "每次添加一批歌，中间会稍等几秒，避免被网易云限制"}
      </p>
      <div className="mt-6 flex items-center gap-3">
        <div className="prog flex-1">
          <i style={{ width: `${pct}%` }} />
        </div>
        <span className="cnt">{pct}%</span>
      </div>
      <div className="list">
        {rows.map((r) => (
          <div key={r.name} className="li">
            <span className="mini" style={toneStyle(r.name, d.other_name)} aria-hidden>
              {coverLabel(r.name).slice(0, 1)}
            </span>
            <span className="t">
              <b>{r.name}</b>
              <span>
                {r.added} / {r.total}
              </span>
            </span>
            <span className={`pill ${stopped && r.status === "添加中" ? "p-warn" : TAG[r.status] ?? "p-gray"}`}>
              {stopped && r.status === "添加中" ? "已中断" : r.status}
            </span>
          </div>
        ))}
      </div>
      {stopped ? (
        <div className="mt-6 flex flex-wrap gap-3">
          <button type="button" className="btn" onClick={resume}>
            继续建
          </button>
          <Link to={to.result(planId)} className="ghost">
            先不建了
          </Link>
        </div>
      ) : (
        <div className="banner">
          <div className="t">
            <b>可以关掉这个网页</b>
            <div className="small">只要启动理音的终端窗口还开着，它会在后台继续建完</div>
          </div>
        </div>
      )}
      {problem && <p className="mt-3 text-sm text-danger">{problem}</p>}
      {flow.dialog}
    </div>
  );
}
