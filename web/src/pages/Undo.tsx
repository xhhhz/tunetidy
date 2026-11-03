import { useCallback, useState } from "react";

import { post, type ApiError, type ConfirmStatus, type Ticket, type UndoPreview } from "../api";
import { useCodeFlow } from "../components/CodeDialog";
import { useLoad } from "../hooks";
import { navigate, to } from "../router";
import { Back, CopyCommand, Icon, formatDay } from "../ui";
import { useJob } from "./Progress";

const ACTION: Record<UndoPreview["items"][number]["action"], [string, string]> = {
  delete: ["删除", "p-gray"],
  remove_ours: ["被改过", "p-warn"],
  skip: ["跳过", "p-gray"],
};

/** 撤销一次写回：先回读预览，确认码绑定预览结果；带 job 参数时显示撤销进度和结果。 */
export function Undo({ runId, jobId }: { runId: string; jobId: string | null }) {
  return jobId ? <UndoResult runId={runId} jobId={jobId} /> : <UndoPreviewPage runId={runId} />;
}

function UndoPreviewPage({ runId }: { runId: string }) {
  const id = encodeURIComponent(runId);
  const preview = useLoad<UndoPreview>(`/api/runs/${id}/undo`);
  const confirm = useLoad<ConfirmStatus>("/api/confirm");
  const [problem, setProblem] = useState("");
  const reloadPreview = preview.reload;
  const onError = useCallback(
    (e: ApiError) => {
      setProblem(e.message);
      if (e.code === "stale") void reloadPreview();
    },
    [reloadPreview],
  );
  const flow = useCodeFlow({ title: "确认撤销", okLabel: "撤销", onError });

  if (preview.error && !preview.data) {
    return (
      <div className="wrap">
        <Back to={to.history()}>历史</Back>
        <p className="sub">{preview.error.message}</p>
      </div>
    );
  }
  const p = preview.data;
  if (!p) return <div className="wrap small">正在检查这些歌单现在的样子…</div>;
  const web = (confirm.data?.available ?? false) && (confirm.data?.locked_seconds ?? 0) === 0;

  const start = () => {
    setProblem("");
    void flow.start(
      () => post<Ticket>(`/api/runs/${id}/undo/confirm`, { fingerprint: p.fingerprint }),
      async (code) => {
        const r = await post<{ job_id: string }>(`/api/runs/${id}/undo/submit`, {
          code,
          fingerprint: p.fingerprint,
        });
        navigate(to.undo(runId, r.job_id), true);
      },
    );
  };

  return (
    <div className="wrap">
      <Back to={to.history()}>历史</Back>
      <h1>撤销 {formatDay(p.created_at)} 的整理</h1>
      <p className="sub">已经检查过这些歌单现在的样子，撤销时会这样处理</p>
      <div className="list">
        {p.items.map((it) => {
          const [tag, cls] = ACTION[it.action];
          const text =
            it.action === "delete"
              ? "和建的时候一样：整个删掉"
              : it.action === "remove_ours"
                ? `你后来改过这个歌单。只移除理音加的 ${it.remove} 首，歌单和你加的歌留着`
                : (it.note ?? "跳过");
          return (
            <div key={it.category} className="li">
              <span className="t">
                <b>{it.name}</b>
                <span>{text}</span>
                {it.differences.length > 0 && (
                  <span className="block text-xs text-fg3">{it.differences.join("；")}</span>
                )}
              </span>
              <span className={`pill ${cls}`}>{tag}</span>
            </div>
          );
        })}
      </div>
      <div className="assure">
        <div>
          <Icon name="shield" />
          <b>不碰你原来的歌单</b>只处理理音建的这 {p.items.length} 个
        </div>
      </div>
      <div className="mt-8">
        {p.nothing_to_do ? (
          <p className="sub">没有需要撤销的内容</p>
        ) : web ? (
          <>
            <button type="button" className="btn" onClick={start}>
              确认撤销
            </button>
            <p className="small mt-3">点了之后，确认码会显示在启动理音的终端窗口里</p>
          </>
        ) : (
          <>
            <p className="mb-3 text-sm text-fg2">
              {(confirm.data?.locked_seconds ?? 0) > 0
                ? `试错太多次，网页确认已暂停，${Math.ceil((confirm.data?.locked_seconds ?? 0) / 60)} 分钟后恢复。也可以在终端里运行`
                : "网页确认不可用：理音不是在终端窗口里启动的。请在终端里运行"}
            </p>
            <CopyCommand command={`tunetidy undo ${runId}`} />
          </>
        )}
        {problem && <p className="mt-3 text-sm text-danger">{problem}</p>}
      </div>
      {flow.dialog}
    </div>
  );
}

function UndoResult({ runId, jobId }: { runId: string; jobId: string }) {
  const job = useJob(jobId);
  if (!job) return <div className="wrap small">正在读取进度…</div>;
  return (
    <div className="wrap">
      <Back to={to.history()}>历史</Back>
      {job.status === "running" && (
        <>
          <h1>正在撤销</h1>
          <p className="sub">删除歌单前会再核对一次，被你改过的只移除理音加的歌</p>
        </>
      )}
      {job.status === "done" && (
        <>
          <h1>已撤销</h1>
          <div className="list">
            {(job.deleted ?? []).map((n) => (
              <div key={n} className="li">
                <span className="t">
                  <b>{n}</b>
                  <span>已删除</span>
                </span>
              </div>
            ))}
            {(job.trimmed ?? []).map((t) => (
              <div key={t.name} className="li">
                <span className="t">
                  <b>{t.name}</b>
                  <span>只移除了理音加的 {t.removed} 首，歌单保留</span>
                </span>
              </div>
            ))}
            {(job.skipped ?? []).map((n) => (
              <div key={n} className="li">
                <span className="t">
                  <b>{n}</b>
                  <span>跳过</span>
                </span>
              </div>
            ))}
          </div>
          {(job.warnings ?? []).map((w) => (
            <p key={w} className="mt-3 text-sm text-warn">
              {w}
            </p>
          ))}
          <button type="button" className="btn mt-8" onClick={() => navigate(to.history())}>
            完成
          </button>
        </>
      )}
      {(job.status === "failed" || job.status === "interrupted") && (
        <>
          <h1>没有撤销</h1>
          <p className="sub">{job.error ?? job.message}</p>
          <button type="button" className="btn mt-6" onClick={() => navigate(to.undo(runId), true)}>
            重新预览
          </button>
        </>
      )}
    </div>
  );
}
