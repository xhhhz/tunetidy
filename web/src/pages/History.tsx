import type { RunRow } from "../api";
import { useLoad } from "../hooks";
import { navigate, to } from "../router";
import { describeShort } from "../split";
import { GenreChips, formatDay } from "../ui";

/** 历史：理音在你的网易云里建过的所有歌单，可以从这里撤销。 */
export function History() {
  const { data, error } = useLoad<RunRow[]>("/api/runs");
  if (error && !data) return <div className="wrap small">{error.message}</div>;
  if (!data) return null;
  return (
    <div className="wrap">
      <h1>历史</h1>
      <p className="sub">理音在你的网易云里建过的所有歌单</p>
      {data.length === 0 && <p className="sub mt-8">还没有建过歌单</p>}
      {data.map((r) => {
        const songs = r.playlists.reduce((n, p) => n + p.added, 0);
        const undone = r.status === "undone";
        const unfinished = r.status === "interrupted" || r.status === "running";
        return (
          <div key={r.run_id} className={`card home-card${undone ? " opacity-60" : ""}`}>
            <div className="min-w-0">
              <h2 className="truncate">{r.sources.join(" + ")}</h2>
              <div className="small mt-1">
                {formatDay(r.created_at)} · {describeShort(r.split)} · 建了 {r.playlists.length} 个歌单、{songs} 首
              </div>
              <GenreChips names={r.playlists.map((p) => p.category)} otherName={r.other_name} />
            </div>
            {undone ? (
              <span className="pill p-gray">已撤销</span>
            ) : unfinished ? (
              <button type="button" className="ghost" onClick={() => navigate(to.confirm(r.plan_id))}>
                没建完
              </button>
            ) : (
              <button type="button" className="ghost" onClick={() => navigate(to.undo(r.run_id))}>
                撤销
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
