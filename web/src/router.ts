// 极简路由：用 history API，服务端对非 /api/ 路径都返回 index.html。
// 分类名可能含 &、/、# 等字符，所以放在查询参数里，用 URLSearchParams 编解码。

import { useEffect, useState } from "react";

export type SongsFilter = "all" | "manual" | "unsure" | "ungrouped" | "unavailable";
export type SongsView = { playlist: string } | { filter: SongsFilter };

export type Route =
  | { name: "home" }
  | { name: "result"; planId: string }
  | { name: "songs"; planId: string; show: SongsView }
  | { name: "pending"; planId: string }
  | { name: "split"; planId: string; more: boolean } // more：从按大类/细类进来，只显示剩下的歌和更多设置
  | { name: "confirm"; planId: string };

const FILTERS: SongsFilter[] = ["all", "manual", "unsure", "ungrouped", "unavailable"];

function parse(path: string, search: string): Route {
  const m = path.match(/^\/plans\/([^/]+)(?:\/([a-z]+))?\/?$/);
  if (!m) return { name: "home" };
  const planId = decodeURIComponent(m[1]);
  const q = new URLSearchParams(search);
  switch (m[2]) {
    case undefined:
      return { name: "result", planId };
    case "songs": {
      const playlist = q.get("playlist");
      const filter = q.get("filter") as SongsFilter | null;
      if (playlist !== null) return { name: "songs", planId, show: { playlist } };
      return { name: "songs", planId, show: { filter: filter && FILTERS.includes(filter) ? filter : "all" } };
    }
    case "pending":
      return { name: "pending", planId };
    case "split":
      return { name: "split", planId, more: q.get("more") === "1" };
    case "confirm":
    case "preview": // 旧网址
      return { name: "confirm", planId };
    default:
      return { name: "result", planId };
  }
}

const plan = (id: string) => `/plans/${encodeURIComponent(id)}`;

/** 各页面的网址。 */
export const to = {
  home: () => "/",
  result: (id: string) => plan(id),
  songs: (id: string, show: SongsView) =>
    `${plan(id)}/songs?${new URLSearchParams("playlist" in show ? { playlist: show.playlist } : { filter: show.filter })}`,
  pending: (id: string) => `${plan(id)}/pending`,
  split: (id: string, more: boolean) => `${plan(id)}/split${more ? "?more=1" : ""}`,
  confirm: (id: string) => `${plan(id)}/confirm`,
};

export function navigate(path: string, replace = false): void {
  if (replace) history.replaceState(null, "", path);
  else history.pushState(null, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
  document.querySelector(".main")?.scrollTo(0, 0);
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(location.pathname, location.search));
  useEffect(() => {
    const on = () => setRoute(parse(location.pathname, location.search));
    window.addEventListener("popstate", on);
    return () => window.removeEventListener("popstate", on);
  }, []);
  return route;
}
