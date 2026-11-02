import { useEffect, useRef } from "react";

import type { PlanRow } from "../api";
import { useLoad } from "../hooks";
import { navigate, to } from "../router";
import { describeShort } from "../split";
import { CopyCommand, GenreChips, StatusBadge, formatDay } from "../ui";

const POLL_MS = 5000;

/** 同一个歌单（或同一组歌单）的整理归为一组；来源顺序不影响分组。 */
export const sourceKey = (r: PlanRow) => [...r.source_ids].sort().join("\u0000");

const unbuilt = (r: PlanRow) => r.status === "draft" || r.status === "approved";

/** 每个来源取最近更新的一份（接口已按更新时间倒序）；还没建歌单的排前面。 */
export function latestBySource(rows: PlanRow[]): PlanRow[] {
  const seen = new Set<string>();
  const latest = rows.filter((r) => !seen.has(sourceKey(r)) && seen.add(sourceKey(r)));
  return [...latest.filter(unbuilt), ...latest.filter((r) => !unbuilt(r))];
}

function summary(r: PlanRow): string {
  const parts = [`${r.total} 首`, describeShort(r.split)];
  if (unbuilt(r)) {
    parts.push(`将新建 ${r.categories.length} 个歌单`);
    if (r.pending > 0) parts.push(`还有 ${r.pending} 首没分好`);
  } else {
    parts.push(`${formatDay(r.updated_at)}建了 ${r.categories.length} 个歌单`);
  }
  return parts.join(" · ");
}

/** 首页：每个来源一张卡片。 */
export function Home() {
  const { data, error, reload } = useLoad<PlanRow[]>("/api/plans");
  const empty = data !== null && data.length === 0;

  // 还没整理过：每 5 秒看一次，终端里整理完就直接打开结果
  useEffect(() => {
    if (!empty) return;
    const t = setInterval(() => void reload(), POLL_MS);
    return () => clearInterval(t);
  }, [empty, reload]);
  const wasEmpty = useRef(false);
  useEffect(() => {
    if (empty) wasEmpty.current = true;
    else if (data && wasEmpty.current) navigate(to.result(data[0].id), true);
  }, [data, empty]);

  if (error && !data) return <div className="wrap small">{error.message}</div>;
  if (!data) return null;
  if (empty) return <Empty />;

  return (
    <div className="wrap">
      <h1>整理</h1>
      {latestBySource(data).map((r) => (
        <button key={r.id} type="button" className="card home-card" onClick={() => navigate(to.result(r.id))}>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="truncate">{r.sources.join(" + ")}</h2>
              <StatusBadge status={r.status} />
            </div>
            <div className="small mt-1">{summary(r)}</div>
            <GenreChips names={r.categories.map((c) => c.name)} otherName={r.other_name} />
          </div>
          <span className={unbuilt(r) ? "btn sm" : "ghost"}>{unbuilt(r) ? "继续" : "查看"}</span>
        </button>
      ))}
      <p className="small mt-6">每个歌单只显示最近一次整理，更早的在结果页右上角「以前的整理」里</p>
    </div>
  );
}

function Empty() {
  return (
    <div className="center">
      <div className="wrap narrow" style={{ paddingBottom: 40 }}>
        <div className="strip justify-center">
          {["流行", "R&B", "摇滚", "电子", "其他"].map((n, i) => (
            <span key={n} style={{ background: `var(--t${i === 4 ? 0 : i + 1})`, color: `var(--i${i === 4 ? 0 : i + 1})` }}>
              {n}
            </span>
          ))}
        </div>
        <h1 className="mt-6">还没有整理过歌单</h1>
        <p className="sub">在终端运行下面的命令，整理完成后结果会自动显示在这里</p>
        <div className="mt-6 flex flex-col gap-3 text-left">
          <CopyCommand command="tunetidy playlists" />
          <CopyCommand command={'tunetidy analyze "歌单名"'} />
        </div>
        <p className="small mt-3">还没登录的话，先运行 tunetidy login</p>
      </div>
    </div>
  );
}
