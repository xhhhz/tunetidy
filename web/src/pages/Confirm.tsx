import type { PlanDetail, Preview } from "../api";
import { useLoad, usePlanState } from "../hooks";
import { to } from "../router";
import { Back, CopyCommand, Icon, Link, toneStyle, coverLabel } from "../ui";

/**
 * 确认建歌单。v1.0 网页上没有确认按钮：确认和建歌单都在终端里做（要输入确认码），
 * 网页每 5 秒读一次状态，终端里确认、建好后自动切换。
 */
export function Confirm({ planId }: { planId: string }) {
  const id = encodeURIComponent(planId);
  const preview = useLoad<Preview>(`/api/plans/${id}/preview`);
  const detail = useLoad<PlanDetail>(`/api/plans/${id}`);
  const reload = preview.reload;
  const { state } = usePlanState(planId, preview.data?.content_hash, () => void reload());

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

  return (
    <div className="wrap">
      <Back to={to.result(planId)}>返回修改</Back>
      <h1>确认要建这 {n} 个歌单吗？</h1>
      <p className="sub">确认后会在你的网易云账号里新建这些歌单</p>

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
          <b>可以撤销</b>不满意的话，在终端运行 tunetidy undo {planId} 删掉
        </div>
      </div>

      <div className="card mt-8">
        {n === 0 ? (
          <p>
            按现在的分法没有歌能放进新歌单，<Link to={to.result(planId)}>返回修改</Link>
          </p>
        ) : status === "applying" ? (
          <>
            <h2>正在创建</h2>
            <p className="sub">完成后这里会自动更新</p>
          </>
        ) : status === "applied" || status === "undone" ? (
          <>
            <h2>{status === "applied" ? "已建好" : "已撤销"}</h2>
            <p className="sub">
              <Link to={to.result(planId)}>回到整理结果</Link>
            </p>
          </>
        ) : (
          <>
            <h2>在终端里运行这两条命令</h2>
            <div className="mt-4 flex flex-col gap-3">
              <div>
                <div className="small mb-1 flex items-center gap-2">
                  1. 确认
                  {status === "approved" && state?.approved !== false && <span className="pill p-ok">已确认</span>}
                </div>
                <CopyCommand command={`tunetidy approve ${planId}`} />
              </div>
              <div>
                <div className="small mb-1">2. 建歌单</div>
                <CopyCommand command={`tunetidy apply ${planId}`} />
              </div>
            </div>
            <p className="small mt-3">终端会让你输入确认码，网页会自动更新</p>
          </>
        )}
      </div>
    </div>
  );
}
