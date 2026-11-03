import { useEffect, useSyncExternalStore, type ReactNode } from "react";

import { connection, get } from "./api";
import { useMe } from "./hooks";
import { to, type Route } from "./router";
import { DialogHost, Icon, Link, ToastHost } from "./ui";

const RETRY_MS = 5000;

/** 页面框架：桌面左侧导航（整理、历史、新整理，底部是网易云账号），窄屏变顶栏。登录页不带导航。 */
export function Shell({ route, children }: { route: Route; children: ReactNode }) {
  if (route.name === "login") {
    return (
      <div className="main">
        {children}
        <Disconnected />
        <DialogHost />
        <ToastHost />
      </div>
    );
  }
  const inHistory = route.name === "history" || route.name === "undo";
  const isNew = route.name === "new";
  return (
    <div className="app">
      <nav className="nav">
        <div className="logo">
          <i>理</i>
          <span>理音</span>
        </div>
        <Link to={to.home()} className={`nv${inHistory || isNew ? "" : " on"}`}>
          <Icon name="home" size={24} />
          整理
        </Link>
        <Link to={to.history()} className={`nv${inHistory ? " on" : ""}`}>
          <Icon name="clock" size={24} />
          历史
        </Link>
        <Link to={to.newPlan()} className={`nv${isNew ? " on" : ""}`}>
          <Icon name="plus" size={24} />
          新整理
        </Link>
        <Account />
      </nav>
      <main className="main">{children}</main>
      <Disconnected />
      <DialogHost />
      <ToastHost />
    </div>
  );
}

/** 导航底部：登录了显示网易云昵称，没登录给一个去登录的链接。 */
function Account() {
  const me = useMe();
  if (!me) return null;
  if (!me.logged_in) {
    return (
      <Link to={to.login()} className="who link">
        登录网易云
      </Link>
    );
  }
  const name = me.nickname ?? "";
  return (
    <div className="who">
      <div className="av" aria-hidden>
        {[...name][0] ?? "?"}
      </div>
      <div className="min-w-0">
        <b>{name}</b>
        <span>网易云已登录</span>
      </div>
    </div>
  );
}

/** 连不上本地服务时盖住页面，每 5 秒重试；口令失效（服务重启过）时提示用新地址打开。 */
function Disconnected() {
  const c = useSyncExternalStore(connection.subscribe, connection.get);
  useEffect(() => {
    if (c !== "down") return;
    const t = setInterval(() => {
      get("/api/ping")
        .then(() => location.reload()) // 改动都已存在本地，重新加载最简单可靠
        .catch(() => undefined);
    }, RETRY_MS);
    return () => clearInterval(t);
  }, [c]);
  if (c === "ok") return null;
  return (
    <div className="scrim frost">
      <div className="modal text-center" role="alertdialog" aria-label="连不上理音了">
        {c === "down" ? (
          <>
            <Icon name="spinner" size={36} className="spin mx-auto text-fg2" />
            <h2 className="mt-3">连不上理音了</h2>
            <p className="sub">启动理音的终端窗口可能被关掉了，重新运行下面的命令后页面会自动恢复，你改过的内容都还在</p>
            <div className="term mt-5 text-left">
              <code>tunetidy ui</code>
            </div>
            <p className="small mt-3">正在每 5 秒重新连接…</p>
          </>
        ) : (
          <>
            <h2>理音重启过</h2>
            <p className="sub">请用终端里新显示的地址重新打开</p>
          </>
        )}
      </div>
    </div>
  );
}
