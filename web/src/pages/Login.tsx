import { useEffect, useState } from "react";

import { get, post, type LoginState } from "../api";
import { refreshMe, useMe } from "../hooks";
import { navigate, to } from "../router";
import { Icon } from "../ui";

const POLL_MS = 1000;

/** 二维码点阵画成 SVG：每个黑格一个 1×1 的方块。 */
function QrImage({ rows }: { rows: string[] }) {
  const n = rows.length;
  const d = rows
    .flatMap((row, y) => [...row].map((c, x) => (c === "1" ? `M${x} ${y}h1v1h-1z` : "")))
    .join("");
  return (
    <svg viewBox={`0 0 ${n} ${n}`} shapeRendering="crispEdges" role="img" aria-label="登录二维码">
      <path d={d} fill="#000" />
    </svg>
  );
}

/** 扫码登录：扫码后显示"已扫码"，手机上确认后跳到选歌单；二维码过期可以刷新。 */
export function Login() {
  const me = useMe();
  const [state, setState] = useState<LoginState | null>(null);
  const [problem, setProblem] = useState("");

  const start = () => {
    setProblem("");
    post<LoginState>("/api/login/start", {})
      .then(setState)
      .catch((e: Error) => setProblem(e.message));
  };

  // 已经登录就直接去选歌单；没登录才开始扫码
  useEffect(() => {
    if (me?.logged_in) navigate(to.newPlan(), true);
    else if (me && !state) start();
  }, [me, state]);

  const status = state?.status;
  useEffect(() => {
    if (status !== "waiting" && status !== "scanned") return;
    const t = setInterval(() => {
      get<LoginState>("/api/login")
        .then(setState)
        .catch(() => undefined);
    }, POLL_MS);
    return () => clearInterval(t);
  }, [status]);

  useEffect(() => {
    if (status === "done") void refreshMe().then(() => navigate(to.newPlan(), true));
  }, [status]);

  const scanned = status === "scanned" || status === "done";
  const dead = status === "expired" || status === "failed";
  return (
    <div className="login">
      <div className="l">
        <div className="logo" style={{ padding: 0 }}>
          <i>理</i>
          <span>理音</span>
        </div>
        <h1>
          一千首歌，
          <br />
          整理成几个顺耳的歌单
        </h1>
        <p className="sub" style={{ maxWidth: "30em" }}>
          按网易云给每首歌标的曲风，把大歌单拆成「流行」「R&B」「摇滚」等小歌单，你看过并确认后才会动手
        </p>
        <div className="assure" style={{ maxWidth: 560 }}>
          <div>
            <Icon name="shield" />
            <b>只新建</b>你原来的歌单一首都不会少
          </div>
          <div>
            <Icon name="lock" />
            <b>只在这台电脑</b>没有服务器，登录信息存在系统钥匙串里
          </div>
        </div>
      </div>
      <div className="r">
        <div>
          <div className={`qr${dead ? " off" : ""}`}>
            {state?.qr ? <QrImage rows={state.qr} /> : <span className="spin" style={{ width: 36, height: 36, margin: 82 }} />}
            {scanned && (
              <div className="over">
                <Icon name="check" size={44} />
                已扫码
              </div>
            )}
            {status === "expired" && <div className="over gray">二维码已过期</div>}
          </div>
          {status === "failed" || problem ? (
            <>
              <h2 className="mt-6">没能登录</h2>
              <p className="small mt-1.5">{state?.error ?? problem}</p>
              <button type="button" className="ghost mt-4" onClick={start}>
                重试
              </button>
            </>
          ) : status === "expired" ? (
            <>
              <h2 className="mt-6">二维码已过期</h2>
              <p className="small mt-1.5">点刷新换一个新的二维码</p>
              <button type="button" className="ghost mt-4" onClick={start}>
                刷新
              </button>
            </>
          ) : (
            <>
              <h2 className="mt-6">{scanned ? "在手机上点「确认登录」" : "用网易云音乐 App 扫一扫"}</h2>
              <p className="small mt-1.5">{scanned ? "确认后这里会自动跳转" : "打开 App，点左上角菜单里的「扫一扫」"}</p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
