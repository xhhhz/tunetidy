import { useEffect, useState } from "react";

import { get, post, type LoginState } from "../api";
import { refreshMe, useMe, useMeError } from "../hooks";
import { navigate, to } from "../router";
import { AppIcon, Icon } from "../ui";

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

/** 读不到网易云账号（比如断网）：说明原因，给一个重试按钮，不要让页面一直空着。 */
export function MeUnavailable({ error }: { error: string }) {
  return (
    <div className="center">
      <div className="wrap narrow">
        <h1>连不上网易云</h1>
        <p className="sub">{error}。检查一下网络，再点重试</p>
        <button type="button" className="btn mt-6" onClick={() => void refreshMe()}>
          重试
        </button>
      </div>
    </div>
  );
}

/** 扫码登录：扫码后显示"已扫码"，手机上确认后跳到选歌单；二维码过期可以刷新。 */
export function Login() {
  const me = useMe();
  const meError = useMeError();
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
    // idle：开始登录后一段时间还没拿到二维码（取码请求被限速），接着等
    if (status !== "idle" && status !== "waiting" && status !== "scanned") return;
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

  if (meError && !me) return <MeUnavailable error={meError} />;
  const scanned = status === "scanned" || status === "done";
  const dead = status === "expired" || status === "failed";
  return (
    <div className="login">
      <div className="l">
        <div className="logo" style={{ padding: 0 }}>
          <AppIcon />
          <span>
            理音<span className="font-medium text-fg2">TuneTidy</span>
          </span>
        </div>
        <div className="hero">
          {/* "乐"读 yuè；先"理"后"音"，藏着产品名 */}
          <h1>
            理乐有方，
            <br />
            顺音成章
          </h1>
          <p className="hs">让每首歌，都找到属于它的地方</p>
        </div>
        <div className="assure" style={{ maxWidth: 560 }}>
          <div>
            <Icon name="shield" />
            <b>只新建</b>不改动你原来的歌单
          </div>
          <div>
            <Icon name="lock" />
            <b>只在这台电脑</b>不经过任何服务器
          </div>
        </div>
      </div>
      <div className="r">
        <div>
          <div className={`qr${dead ? " off" : ""}`}>
            {state?.qr ? <QrImage rows={state.qr} /> : <span className="spin" style={{ width: 36, height: 36, margin: 82 }} />}
            {scanned && (
              <div className="over">
                <span className="ok" aria-hidden>
                  <Icon name="check" size={22} />
                </span>
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
