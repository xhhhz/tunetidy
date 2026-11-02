import { useEffect, useMemo, useRef, useState } from "react";

import type { SongRow } from "../api";
import { useLoad } from "../hooks";
import { navigate, to, type SongsFilter, type SongsView } from "../router";
import { Back, Cover, askText } from "../ui";
import { usePlan } from "./Result";
import { SongTable, songKey } from "./SongTable";

const FILTERS: [SongsFilter, string][] = [
  ["all", "全部歌曲"],
  ["manual", "你改过的"],
  ["unsure", "没分好"],
  ["ungrouped", "不放进的"],
  ["unavailable", "加不进去的"],
];

function inView(r: SongRow, show: SongsView): boolean {
  if ("playlist" in show) return r.status === "assigned" && r.category === show.playlist;
  switch (show.filter) {
    case "all":
      return true;
    case "manual":
      return r.manual;
    case "unsure":
      return r.status === "pending";
    case "ungrouped":
      return r.status === "ungrouped";
    case "unavailable":
      return r.status === "unavailable";
  }
}

const NEW = "\u0000new";
const OUT = "\u0000out";

/** 歌单里的歌（或按筛选看全部歌曲）：搜索、多选、移到别的歌单。 */
export function Songs({ planId, show }: { planId: string; show: SongsView }) {
  const { d, error, editable, edit, extra } = usePlan(planId);
  const songs = useLoad<{ songs: SongRow[] }>(`/api/plans/${encodeURIComponent(planId)}/songs`);
  const reloadSongs = songs.reload;
  useEffect(() => {
    extra.current = reloadSongs;
  }, [extra, reloadSongs]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const lastClicked = useRef<number | null>(null);
  const showKey = JSON.stringify(show);
  useEffect(() => {
    setSelected(new Set());
    lastClicked.current = null;
  }, [showKey]);

  const rows = songs.data?.songs ?? [];
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter(
      (r) => inView(r, show) && (!q || `${r.name} ${r.artists.join(" ")} ${r.album}`.toLowerCase().includes(q)),
    );
  }, [rows, showKey, query]);

  if (error && !d) return <div className="wrap small">{error.message}</div>;
  if (!d) return null;
  const playlist = "playlist" in show ? d.playlists.find((p) => p.category === show.playlist) : undefined;
  const others = d.playlists.filter((p) => !("playlist" in show) || p.category !== show.playlist);

  const toggle = (key: string, shift: boolean) => {
    const idx = visible.findIndex((r) => songKey(r) === key);
    const next = new Set(selected);
    const on = !next.has(key);
    if (shift && lastClicked.current !== null) {
      const [a, b] = [Math.min(lastClicked.current, idx), Math.max(lastClicked.current, idx)];
      for (const r of visible.slice(a, b + 1)) {
        if (r.status === "unavailable") continue;
        if (on) next.add(songKey(r));
        else next.delete(songKey(r));
      }
    } else if (on) next.add(key);
    else next.delete(key);
    lastClicked.current = idx;
    setSelected(next);
  };
  const toggleAll = () => {
    const movable = visible.filter((r) => r.status !== "unavailable").map(songKey);
    setSelected(movable.every((k) => selected.has(k)) ? new Set() : new Set(movable));
  };

  const move = async (category: string | null, done: (n: number) => string) => {
    const keys = rows.filter((r) => selected.has(songKey(r))).map((r) => [r.platform, r.id]);
    const r = await edit.run("move", { songs: keys, category }, () => done(keys.length));
    if (r) setSelected(new Set());
  };
  const moveTo = async (target: string) => {
    if (target === OUT) return move(null, (n) => `已把 ${n} 首设为不放进新歌单`);
    const name =
      target === NEW ? await askText({ title: "新歌单叫什么？", ok: "移过去", input: { placeholder: "比如：睡前", max: 40 } }) : target;
    if (name) await move(name, (n) => `已把 ${n} 首移到「${name}」`);
  };

  return (
    <div className="wrap">
      <Back to={to.result(planId)}>{d.sources.map((s) => s.name).join(" + ")}</Back>
      {"playlist" in show ? (
        <div className="flex items-center gap-5">
          <div className="w-28 shrink-0 sm:w-36">
            <Cover name={show.playlist} otherName={d.other_name} scale={0.55} />
          </div>
          <div className="min-w-0">
            <div className="small">将新建的{d.private ? "私密" : ""}歌单</div>
            <h1 className="mt-1 break-words">{playlist?.name ?? show.playlist}</h1>
            <div className="small mt-1">
              {playlist ? `${playlist.count} 首 · 顺序和原歌单一致` : "这个歌单里没有歌了"}
            </div>
          </div>
        </div>
      ) : (
        <h1>{FILTERS.find(([f]) => f === show.filter)?.[1]}</h1>
      )}

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <input
          className="search min-w-0 flex-1 basis-56"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索歌名、歌手、专辑"
          aria-label="搜索"
        />
        <select
          className="sel lg"
          aria-label="筛选"
          value={"playlist" in show ? `p:${show.playlist}` : `f:${show.filter}`}
          onChange={(e) => {
            const v = e.target.value;
            const next: SongsView = v.startsWith("p:") ? { playlist: v.slice(2) } : { filter: v.slice(2) as SongsFilter };
            navigate(to.songs(planId, next), true);
          }}
        >
          <optgroup label="歌单">
            {d.playlists.map((p) => (
              <option key={p.category} value={`p:${p.category}`}>
                {p.category}（{p.count}）
              </option>
            ))}
          </optgroup>
          <optgroup label="筛选">
            {FILTERS.map(([f, label]) => (
              <option key={f} value={`f:${f}`}>
                {label}
              </option>
            ))}
          </optgroup>
        </select>
        <span className="small">显示 {visible.length} 首</span>
      </div>

      <SongTable
        key={`${showKey}|${query.trim()}`} // 换筛选条件时回到顶部
        rows={visible}
        selected={selected}
        selectable={editable}
        onToggle={toggle}
        onToggleAll={toggleAll}
      />

      {editable && selected.size > 0 && (
        <>
          <div className="selbar" role="toolbar" aria-label="已选歌曲的操作">
            <span>已选 {selected.size} 首</span>
            <select
              value=""
              aria-label="移到"
              disabled={edit.busy}
              onChange={(e) => e.target.value && void moveTo(e.target.value)}
            >
              <option value="">移到…</option>
              {others.map((p) => (
                <option key={p.category} value={p.category}>
                  {p.category}
                </option>
              ))}
              <option value={NEW}>新歌单…</option>
              {!("playlist" in show) && <option value={OUT}>不放进新歌单</option>}
            </select>
            {"playlist" in show && (
              <button
                type="button"
                disabled={edit.busy}
                onClick={() => void move(null, (n) => `已从「${show.playlist}」删除 ${n} 首`)}
              >
                从这个歌单删除
              </button>
            )}
            <button type="button" onClick={() => setSelected(new Set())}>
              取消
            </button>
          </div>
          {"playlist" in show && (
            <p className="small mt-2 text-center">删除只影响这个将要新建的歌单，原来的歌单不会变</p>
          )}
        </>
      )}
    </div>
  );
}
