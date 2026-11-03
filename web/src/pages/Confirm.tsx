import { useCallback, useState } from "react";

import { post, type ApiError, type ConfirmStatus, type PlanDetail, type Preview, type Ticket } from "../api";
import { useCodeFlow } from "../components/CodeDialog";
import { useLoad, usePlanState } from "../hooks";
import { navigate, to } from "../router";
import { Back, CopyCommand, Icon, Link, toneStyle, coverLabel } from "../ui";

/**
 * 确认建歌单。理音在交互式终端里启动时，点"确认并开始建"后确认码显示在那个终端里，
 * 输进网页弹窗就开始建；否则网页确认不可用，给出终端命令。
 */
export function Confirm({ planId }: { planId: string }) {
  const id = encodeURIComponent(planId);
  const preview = useLoad<Preview>(`/api/plans/${id}/preview`);
  const detail = useLoad<PlanDetail>(`/api/plans/${id}`);
  const confirm = useLoad<ConfirmStatus>("/api/confirm");
  const reload = preview.reload;
  const { state } = usePlanState(planId, preview.data?.content_hash, () => void reload());
  const [problem, setProblem] = useState("");
  const onError = useCallback(
    (e: ApiError) => {
      setProblem(e.message);
      void confirm.reload();
      if (e.code === "stale") void reload();
    },
    [confirm, reload],
  );
  const flow = useCodeFlow({ title: "最后一步：确认是你本人", okLabel: "开始建", onError });

  const p = preview.data;
  const d = detail.data;
  if (preview.error && !p) return <div className="wrap small">{preview.error.message}</div>;
  if (!p || !d) return null;
  const status = state?.status ?? p.status;
  const n = p.playlists.length;
  const skipped = [
    p.skipped.pending > 0 && `${p.skipped.pending} 首没分好`,
    p.skipped.unavailable > 0 && `${p.skipped.unavailable} 首已下架或没版权`,
    p.skipped.ungrouped > 0 && `${p.skipped.ungrouped} 首你选了不放进`,
  ].filter(Boolean);
  const web = confirm.data?.available ?? false;
  const locked = confirm.data?.locked_seconds ?? 0;
  const resume = status === "applying";

  const start = () => {
    setProblem("");
    void flow.start(
      () => post<Ticket>(`/api/plans/${id}/confirm`, { expected_hash: state?.content_hash ?? p.content_hash }),
      async (code) => {
        const r = await post<{ job_id: string }>(`/api/plans/${id}/confirm/submit`, { code });
        navigate(to.progress(planId, r.job_id));
      },
    );
  };

  return (
    <div className="wrap">
      <Back to={to.result(planId)}>返回修改</Back>
      <h1>{resume ? "上次没建完" : `确认要建这 ${n} 个歌单吗？`}</h1>
      <p className="sub">
        {resume ? "继续时，已加入的歌不会重复添加" : "确认后会在你的网易云账号里新建这些歌单"}
      </p>

      <div className="list">
        {p.playlists.map((pl) => (
          <div key={pl.category} className="li">
            <span className="mini" style={toneStyle(pl.category, d.other_name)} aria-hidden>
              {coverLabel(pl.category).slice(0, 1)}
            </span>
            <span className="t">
              <b>{pl.name}</b>
              <span>{pl.count} 首</span>
            </span>
            {p.private && (
              <span className="pill p-gray">
                <Icon name="lock" size={12} />
                私密
              </span>
            )}
          </div>
        ))}
      </div>
      <p className="small mt-3">
        共 {p.total} 首放进新歌单
        {skipped.length > 0 && `，另有 ${skipped.join("、")}，不会放进去`}
      </p>

      <div className="assure">
        <div>
          <Icon name="shield" />
          <b>只新建</b>你原来的歌单一首都不会变
        </div>
        <div>
          <Icon name="lock" />
          {p.private ? (
            <>
              <b>都是私密的</b>别人看不到，想公开去网易云里改
            </>
          ) : (
            <>
              <b>会建成公开歌单</b>这是你在设置里选的
            </>
          )}
        </div>
        <div>
          <Icon name="undo" />
          <b>可以撤销</b>不满意的话，在「历史」里撤销
        </div>
      </div>

      <div className="card mt-8">
        {n === 0 ? (
          <p>
            按现在的分法没有歌能放进新歌单，<Link to={to.result(planId)}>返回修改</Link>
          </p>
        ) : status === "applied" || status === "undone" ? (
          <>
            <h2>{status === "applied" ? "已建好" : "已撤销"}</h2>
            <p className="sub">
              <Link to={to.result(planId)}>回到整理结果</Link>
            </p>
          </>
        ) : web && locked === 0 ? (
          <>
            <button type="button" className="btn" onClick={start}>
              {resume ? "继续建" : "确认并开始建"}
            </button>
            <p className="small mt-3">点了之后，确认码会显示在启动理音的终端窗口里</p>
            {problem && <p className="mt-3 text-sm text-danger">{problem}</p>}
          </>
        ) : (
          <>
            {locked > 0 ? (
              <p className="mb-4 text-sm text-warn">
                试错太多次，网页确认已暂停，{Math.ceil(locked / 60)} 分钟后恢复。也可以用下面的终端命令
              </p>
            ) : (
              <p className="mb-4 text-sm text-fg2">
                网页确认不可用：理音不是在终端窗口里启动的，确认码没有地方显示。请在终端里运行
              </p>
            )}
            <div className="flex flex-col gap-3">
              {!resume && (
                <div>
                  <div className="small mb-1">1. 确认</div>
                  <CopyCommand command={`tunetidy approve ${planId}`} />
                </div>
              )}
              <div>
                <div className="small mb-1">{resume ? "继续建" : "2. 建歌单"}</div>
                <CopyCommand command={`tunetidy apply ${planId}`} />
              </div>
            </div>
          </>
        )}
      </div>
      {flow.dialog}
    </div>
  );
}
