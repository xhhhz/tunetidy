import { useEffect, useSyncExternalStore, type ReactNode } from "react";

import { connection, get } from "./api";
import { DialogHost, Icon, Link, ToastHost } from "./ui";

const RETRY_MS = 5000;

/** 页面框架：桌面左侧导航，窄屏变顶栏。目前只有"整理"一项。 */
export function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="app">
      <nav className="nav">
        <div className="logo">
          <i>理</i>
          <span>理音</span>
        </div>
        <Link to="/" className="nv on">
          <Icon name="home" size={24} />
          整理
        </Link>
      </nav>
      <main className="main">{children}</main>
      <Disconnected />
      <DialogHost />
      <ToastHost />
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
