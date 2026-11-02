import { useEffect } from "react";

import type { PlanDetail, SongRow } from "../api";
import type { RunEdit } from "../edit";
import { useLoad } from "../hooks";
import { navigate, to } from "../router";
import { Back, BottomBar, askText } from "../ui";
import { usePlan } from "./Result";

const NEW = "\u0000new";

/** 没分好的歌：每首一行，点一下选个歌单；再点一次已选的就撤回。 */
export function Pending({ planId }: { planId: string }) {
  const { d, error, editable, edit, extra } = usePlan(planId);
  const songs = useLoad<{ songs: SongRow[] }>(`/api/plans/${encodeURIComponent(planId)}/songs`);
  const reloadSongs = songs.reload;
  useEffect(() => {
    extra.current = reloadSongs;
  }, [extra, reloadSongs]);

  if (error && !d) return <div className="wrap small">{error.message}</div>;
  if (!d || !songs.data) return null;
  // 改过之后也留在列表里，方便改主意
  const rows = songs.data.songs.filter((r) => r.unsure);
  const left = d.summary.pending;

  return (
    <>
      <div className="wrap">
        <Back to={to.result(planId)}>{d.sources.map((s) => s.name).join(" + ")}</Back>
        <h1>{left > 0 ? `还有 ${left} 首没分好` : "都分好了"}</h1>
        <p className="sub">网易云给的曲风信息不够，没法自动判断。可以帮它们选个歌单，不选的话不会放进新歌单</p>
        <div className="mt-6 border-t border-line">
          {rows.map((r) => (
            <PendingRow key={`${r.platform}:${r.id}`} row={r} plan={d} editable={editable && !edit.busy} run={edit.run} />
          ))}
        </div>
      </div>
      <BottomBar summary={<b>{left > 0 ? `还有 ${left} 首没分好` : "都分好了"}</b>}>
        <button type="button" className="btn" onClick={() => navigate(to.result(planId))}>
          完成
        </button>
      </BottomBar>
    </>
  );
}

function PendingRow({ row, plan, editable, run }: { row: SongRow; plan: PlanDetail; editable: boolean; run: RunEdit }) {
  const names = plan.playlists.map((p) => p.category);
  let options = row.tied.filter((c) => names.includes(c));
  if (options.length === 0) options = names.slice(0, 2); // 没有可用的并列曲风：给首数最多的两个歌单
  const rest = names.filter((c) => !options.includes(c));
  const current = !row.manual ? null : row.status === "ungrouped" ? "out" : row.category;
  const key: [string, string][] = [[row.platform, row.id]];

  const choose = (target: string | null) => {
    const value = target ?? "out";
    if (value === current) {
      void run("reset", { songs: key }, () => "");
    } else {
      void run("move", { songs: key, category: target }, () => "");
    }
  };
  const pickOther = async (v: string) => {
    if (v !== NEW) return choose(v);
    const name = await askText({ title: "新歌单叫什么？", ok: "放进去", input: { placeholder: "比如：睡前", max: 40 } });
    if (name) choose(name);
  };
  const chip = (on: boolean) => `chip${on ? " on" : ""}`;
  const otherOn = current !== null && current !== "out" && !options.includes(current);

  return (
    <div className="uq">
      <div className="min-w-0">
        <b className="block truncate">{row.name || row.id}</b>
        <div className="small truncate">
          {[row.artists.join(" / "), row.album].filter(Boolean).join(" · ")}
        </div>
        <div className="text-sm text-warn">{row.why}</div>
      </div>
      <div className="ops">
        {options.map((c) => (
          <button key={c} type="button" className={chip(current === c)} disabled={!editable} onClick={() => choose(c)}>
            {c}
          </button>
        ))}
        <label className={chip(otherOn)}>
          <select
            value={otherOn ? current! : ""}
            disabled={!editable}
            aria-label={`把「${row.name}」放进别的歌单`}
            onChange={(e) => e.target.value && void pickOther(e.target.value)}
          >
            <option value="">别的…</option>
            {otherOn && !rest.includes(current!) && <option value={current!}>{current}</option>}
            {rest.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
            <option value={NEW}>新建一个歌单…</option>
          </select>
        </label>
        <button type="button" className={chip(current === "out")} disabled={!editable} onClick={() => choose(null)}>
          先不放
        </button>
      </div>
    </div>
  );
}
