import { useEffect } from "react";

import type { PlanRow } from "../api";
import { useLoad, useMe } from "../hooks";
import { navigate, to } from "../router";
import { describeShort } from "../split";
import { GenreChips, StatusBadge, formatDay } from "../ui";
import { Select } from "./Select";


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

/** 首页：每个来源一张卡片；还没整理过时直接选歌单（没登录先扫码）。 */
export function Home() {
  const { data, error } = useLoad<PlanRow[]>("/api/plans");
  const me = useMe();
  const empty = data !== null && data.length === 0;
  useEffect(() => {
    if (empty && me?.logged_in === false) navigate(to.login(), true);
  }, [empty, me]);

  if (error && !data) return <div className="wrap small">{error.message}</div>;
  if (!data) return null;
  if (empty) return me?.logged_in ? <Select /> : null;

  return (
    <div className="wrap">
      <div className="flex items-center justify-between gap-4">
        <h1>整理</h1>
        <button type="button" className="btn sm" onClick={() => navigate(to.newPlan())}>
          新整理
        </button>
      </div>
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
