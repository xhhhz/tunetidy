import { useVirtualizer } from "@tanstack/react-virtual";
import { useLayoutEffect, useRef, useState } from "react";

import type { SongRow } from "../api";

const ROW_HEIGHT = 56; // 最小行高；标签多、换行时行会变高
const COLS = "grid grid-cols-[2rem_minmax(10rem,2fr)_minmax(6rem,1fr)_minmax(10rem,1.6fr)] gap-x-3 items-center";

export const songKey = (s: SongRow) => `${s.platform}:${s.id}`;

/**
 * 只渲染看得见的行，上千首也不卡。表格跟着整页（.main）一起滚动，不另套一层滚动区，
 * 滚到页底时列表正好铺到底。窄屏时表格在自己的容器里横向滚动。
 */
export function SongTable({
  rows,
  selected,
  selectable,
  onToggle,
  onToggleAll,
}: {
  rows: SongRow[];
  selected: Set<string>;
  selectable: boolean;
  onToggle: (key: string, shift: boolean) => void;
  onToggleAll: () => void;
}) {
  const list = useRef<HTMLDivElement>(null);
  const [offset, setOffset] = useState(0); // 列表顶端到页面滚动区顶端的距离
  useLayoutEffect(() => {
    const el = list.current;
    const main = el?.closest(".main");
    if (el && main) setOffset(el.getBoundingClientRect().top - main.getBoundingClientRect().top + main.scrollTop);
  });
  const v = useVirtualizer({
    count: rows.length,
    getScrollElement: () => list.current?.closest(".main") ?? null,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
    scrollMargin: offset,
    measureElement: (el) => el.getBoundingClientRect().height, // 按实际行高排布
  });
  const movable = rows.filter((r) => r.status !== "unavailable");
  const allSelected = movable.length > 0 && movable.every((r) => selected.has(songKey(r)));

  return (
    <div className="mt-4 overflow-x-auto text-sm">
      <div className="min-w-[34rem]">
        <div className={`${COLS} border-b border-line px-1 py-2 text-xs text-fg3`}>
          <span>
            {selectable && movable.length > 0 && (
              <input type="checkbox" aria-label="全选当前筛选结果" checked={allSelected} onChange={onToggleAll} />
            )}
          </span>
          <span>歌曲</span>
          <span>专辑</span>
          <span>曲风标签</span>
        </div>
        {rows.length === 0 && <p className="small p-4">没有符合条件的歌</p>}
        <div ref={list} style={{ height: v.getTotalSize(), position: "relative" }} data-testid="song-list">
          {v.getVirtualItems().map((item) => {
            const r = rows[item.index];
            const key = songKey(r);
            const canSelect = selectable && r.status !== "unavailable";
            const isSel = selected.has(key);
            return (
              <div
                key={key}
                ref={v.measureElement}
                data-index={item.index}
                className={`${COLS} absolute inset-x-0 border-b border-line px-1 py-2 ${isSel ? "bg-accent-bg" : ""} ${
                  r.status === "unavailable" ? "text-fg3" : ""
                }`}
                style={{ minHeight: ROW_HEIGHT, transform: `translateY(${item.start - offset}px)` }}
              >
                <span>
                  {canSelect && (
                    <input
                      type="checkbox"
                      aria-label={`选择 ${r.name}`}
                      checked={isSel}
                      onChange={() => undefined}
                      onClick={(e) => onToggle(key, e.shiftKey)}
                    />
                  )}
                </span>
                <span className="min-w-0">
                  <span className="flex items-center gap-2">
                    <b className="truncate font-semibold" title={r.name}>
                      {r.name || r.id}
                    </b>
                    {r.manual && <span className="pill p-acc shrink-0">你改过</span>}
                  </span>
                  <span className="block truncate text-xs text-fg2" title={r.artists.join(" / ")}>
                    {r.artists.join(" / ")}
                  </span>
                </span>
                <span className="truncate text-fg2" title={r.album}>
                  {r.album}
                </span>
                <span className="flex flex-wrap gap-1" title={r.reason}>
                  {r.genres.length > 0 ? (
                    // 同一种灰底，只用字色深浅区分：分类时用上的字深，没用上的字浅
                    r.genres.map((g) => (
                      <span
                        key={g.name}
                        className={`pill bg-soft ${g.used ? "text-fg2" : "font-normal text-fg3"}`}
                        title={g.used ? undefined : "分类时没用上这个标签"}
                      >
                        {g.name}
                      </span>
                    ))
                  ) : (
                    <span className="text-fg3">没有曲风标签</span>
                  )}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
