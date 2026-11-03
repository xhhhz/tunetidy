import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { ApiError, type Ticket } from "../api";
import { Button } from "../ui";

const DIGITS = 6;

interface DialogProps {
  title: string;
  okLabel: string;
  ticket: Ticket;
  /** 提交确认码；失败时抛 ApiError（说明文字由服务端给出，从不含码）。 */
  onSubmit: (code: string) => Promise<void>;
  onClose: () => void;
}

/**
 * "最后一步：确认是你本人"：确认码只显示在启动理音的终端里，这里只负责让人把它输进来。
 * 6 个数字格：输入一位自动跳到下一格，退格回到上一格，可以直接粘贴，回车提交。
 */
export function CodeDialog({ title, okLabel, ticket, onSubmit, onClose }: DialogProps) {
  const [digits, setDigits] = useState<string[]>(Array(DIGITS).fill(""));
  const [error, setError] = useState("");
  const [dead, setDead] = useState(false); // 码已作废或过期：只能关掉重新申请
  const [busy, setBusy] = useState(false);
  const [left, setLeft] = useState(ticket.expires_in);
  const boxes = useRef<(HTMLInputElement | null)[]>([]);

  useEffect(() => boxes.current[0]?.focus(), []);
  useEffect(() => {
    const t = setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    if (left === 0 && !dead) {
      setDead(true);
      setError("确认码已过期，关掉弹窗重新点一次");
    }
  }, [left, dead]);

  const code = digits.join("");
  const submit = async () => {
    if (code.length !== DIGITS || busy || dead) return;
    setBusy(true);
    try {
      await onSubmit(code);
    } catch (e) {
      const err = e as ApiError;
      setError(err.message);
      setDigits(Array(DIGITS).fill(""));
      // 还能再试：留在弹窗里；其余（作废、过期、锁定、内容变了）只能关掉重来
      if (!(err.code === "wrong" && err.message.includes("还能再试"))) setDead(true);
      else boxes.current[0]?.focus();
    } finally {
      setBusy(false);
    }
  };

  const put = (i: number, value: string) => {
    const only = value.replace(/\D/g, "");
    if (only.length > 1) {
      // 粘贴了一串：从第 i 格开始依次填
      const next = [...digits];
      only.slice(0, DIGITS - i).split("").forEach((d, k) => (next[i + k] = d));
      setDigits(next);
      boxes.current[Math.min(i + only.length, DIGITS - 1)]?.focus();
      return;
    }
    const next = [...digits];
    next[i] = only;
    setDigits(next);
    setError("");
    if (only && i < DIGITS - 1) boxes.current[i + 1]?.focus();
  };

  return (
    <div className="scrim" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal aria-label={title} onClick={(e) => e.stopPropagation()}>
        <h2>{title}</h2>
        <p className="sub">切换到启动理音的终端窗口，把里面显示的 {DIGITS} 位确认码输入到下面</p>
        <div className="mt-4 rounded-xl bg-soft p-3 text-sm">
          {ticket.summary.map((line, i) => (
            <div key={i} className={i === 0 ? "font-semibold" : "text-fg2"}>
              {line}
            </div>
          ))}
        </div>
        <div className={`digits mt-5${error && !dead ? " bad" : ""}`}>
          {digits.map((d, i) => (
            <input
              key={i}
              ref={(el) => {
                boxes.current[i] = el;
              }}
              value={d}
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={DIGITS}
              aria-label={`确认码第 ${i + 1} 位`}
              disabled={dead || busy}
              onChange={(e) => put(i, e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Backspace" && !digits[i] && i > 0) boxes.current[i - 1]?.focus();
                if (e.key === "Enter") void submit();
                if (e.key === "Escape") onClose();
              }}
            />
          ))}
        </div>
        <div className={error ? "err" : "err text-fg3"} role="alert">
          {error || (dead ? "" : `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} 后过期`)}
        </div>
        <p className="small mt-2">为什么要这样做？电脑上的 AI 助手也能点网页按钮，但看不到你的终端</p>
        <div className="mt-5 flex justify-end gap-3">
          <Button onClick={onClose}>{dead ? "关闭" : "取消"}</Button>
          <Button primary small disabled={code.length !== DIGITS || busy || dead} onClick={() => void submit()}>
            {okLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}

/**
 * 申请确认码 → 弹窗输入 → 提交。request 返回回执（不含码）；submit 返回成功后要做的事。
 * 申请失败（不可用、锁定、限频、内容变了）时调用 onError。
 */
export function useCodeFlow(opts: {
  title: string;
  okLabel: string;
  onError: (e: ApiError) => void;
}): { start: (request: () => Promise<Ticket>, submit: (code: string) => Promise<void>) => Promise<void>; dialog: ReactNode } {
  const [state, setState] = useState<{ ticket: Ticket; submit: (code: string) => Promise<void> } | null>(null);
  const { onError } = opts;
  const start = useCallback(
    async (request: () => Promise<Ticket>, submit: (code: string) => Promise<void>) => {
      try {
        setState({ ticket: await request(), submit });
      } catch (e) {
        onError(e as ApiError);
      }
    },
    [onError],
  );
  const dialog = state ? (
    <CodeDialog
      title={opts.title}
      okLabel={opts.okLabel}
      ticket={state.ticket}
      onSubmit={async (code) => {
        await state.submit(code);
        setState(null);
      }}
      onClose={() => setState(null)}
    />
  ) : null;
  return { start, dialog };
}
