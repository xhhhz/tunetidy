import { useEffect, useState } from "react";

import { post, type ApiError, type PlaylistRow } from "../api";
import { useLoad, useMe } from "../hooks";
import { navigate, to } from "../router";
import { BottomBar, Button, Icon, Waiting } from "../ui";

const SECONDS_PER_SONG = 1.5; // 第一次读取 1000 首约 25 分钟

/** 勾选框：只选一个时打勾，选多个时显示勾选的先后（合并顺序）。 */
function Row({ p, order, multi, onToggle }: { p: PlaylistRow; order: number; multi: boolean; onToggle: () => void }) {
  const on = order > 0;
  return (
    <button type="button" className="li" onClick={onToggle} aria-pressed={on}>
      <span className={`chk${on ? " on" : ""}`}>{on && (multi ? order : <Icon name="check" size={14} />)}</span>
      <span className="mini" style={{ background: "var(--soft)", color: "var(--fg2)" }}>
        <Icon name="note" size={20} />
      </span>
      <span className="t">
        <b>{p.name}</b>
        <span>{p.track_count} 首</span>
      </span>
    </button>
  );
}

/** 选歌单：默认勾选"我喜欢的音乐"；勾选多个时，按勾选的先后合并再拆分。 */
export function Select() {
  const me = useMe();
  const list = useLoad<{ playlists: PlaylistRow[] }>("/api/playlists");
  const [picked, setPicked] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");

  const notLoggedIn = me?.logged_in === false || list.error?.code === "not_logged_in";
  useEffect(() => {
    if (notLoggedIn) navigate(to.login(), true);
  }, [notLoggedIn]);

  const rows = list.data?.playlists;
  // 网易云把"我喜欢的音乐"排在自己创建的歌单最前面
  useEffect(() => {
    if (rows && picked === null) setPicked(rows.filter((p) => p.owned).slice(0, 1).map((p) => p.id));
  }, [rows, picked]);

  if (list.error && !rows && !notLoggedIn) return <div className="wrap small">{list.error.message}</div>;
  if (!rows || picked === null) {
    // 读歌单列表要请求网易云几次（每次之间有限速间隔），先把页面标题和等待提示显示出来
    return (
      <div className="wrap">
        <h1>整理哪些歌单？</h1>
        <Waiting>正在读取你的歌单…</Waiting>
      </div>
    );
  }

  const toggle = (id: string) =>
    setPicked(picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id]);
  const chosen = rows.filter((p) => picked.includes(p.id));
  const songs = chosen.reduce((n, p) => n + p.track_count, 0);
  const minutes = Math.max(1, Math.ceil((songs * SECONDS_PER_SONG) / 60));

  const start = () => {
    setBusy(true);
    setProblem("");
    post<{ job_id: string }>("/api/analyze", { playlists: picked })
      .then((r) => navigate(to.reading(r.job_id)))
      .catch((e: ApiError) => {
        setProblem(e.message);
        setBusy(false);
      });
  };

  const group = (title: string, items: PlaylistRow[]) =>
    items.length > 0 && (
      <>
        <div className="sect">{title}</div>
        <div className="list" style={{ marginTop: 0 }}>
          {items.map((p) => (
            <Row
              key={p.id}
              p={p}
              order={picked.indexOf(p.id) + 1}
              multi={picked.length > 1}
              onToggle={() => toggle(p.id)}
            />
          ))}
        </div>
      </>
    );

  return (
    <>
      <div className="wrap">
        <h1>整理哪些歌单？</h1>
        <p className="sub">默认整理「我喜欢的音乐」，选多个歌单会把其中的歌合并，再按曲风拆分</p>
        {group("你创建的", rows.filter((p) => p.owned))}
        {group("你收藏的", rows.filter((p) => !p.owned))}
        {rows.length === 0 && <p className="small mt-6">你的网易云里还没有歌单</p>}
      </div>
      <BottomBar
        summary={
          <>
            <b>
              已选 {chosen.length} 个 · {chosen.length > 1 ? "最多 " : ""}
              {songs} 首
            </b>
            <div className="small">
              {problem ? (
                <span className="text-danger">{problem}</span>
              ) : chosen.length > 1 ? (
                `按勾选的先后合并，重复的歌只算一次 · 第一次读取最多大约 ${minutes} 分钟，读过的歌会跳过`
              ) : chosen.length === 1 ? (
                `第一次读取最多大约 ${minutes} 分钟，读过的歌会跳过`
              ) : (
                "至少选一个歌单"
              )}
            </div>
          </>
        }
      >
        <Button primary onClick={start} disabled={chosen.length === 0 || busy}>
          开始整理
        </Button>
      </BottomBar>
    </>
  );
}
