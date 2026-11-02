import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";

import { STATUS_TEXT, type Status } from "./api";
import { navigate } from "./router";

// ---------------------------------------------------------------- 图标（手画线性 SVG）

const ICONS = {
  home: <path d="M4 11l8-7 8 7v9a1 1 0 0 1-1 1h-5v-6h-4v6H5a1 1 0 0 1-1-1z" />,
  pen: <path d="M4 20h4L19 9l-4-4L4 16z" />,
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  shield: (
    <>
      <path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z" />
      <path d="M8.5 12l2.5 2.5 4.5-5" />
    </>
  ),
  lock: (
    <>
      <rect x="5" y="11" width="14" height="9" rx="2" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </>
  ),
  undo: (
    <>
      <path d="M9 7L4 12l5 5" />
      <path d="M4 12h10a6 6 0 0 1 0 12h-2" transform="translate(0 -6)" />
    </>
  ),
  x: <path d="M7 7l10 10M17 7L7 17" />,
  back: <path d="M15 5l-7 7 7 7" />,
  next: <path d="M9 5l7 7-7 7" />,
  up: <path d="M6 15l6-6 6 6" />,
  down: <path d="M6 9l6 6 6-6" />,
  copy: (
    <>
      <rect x="8" y="8" width="12" height="12" rx="2" />
      <path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" />
    </>
  ),
  spinner: <path d="M12 3a9 9 0 1 0 9 9" />,
};

export type IconName = keyof typeof ICONS;

export function Icon({ name, size = 18, className }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={name === "check" ? 3 : 1.9}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      {ICONS[name]}
    </svg>
  );
}

// ---------------------------------------------------------------- 基本元素

export function Link({ to, children, className }: { to: string; children: ReactNode; className?: string }) {
  return (
    <a
      href={to}
      className={className ?? "link"}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey) return;
        e.preventDefault();
        navigate(to);
      }}
    >
      {children}
    </a>
  );
}

export function Back({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="back">
      <Icon name="back" size={16} />
      {children}
    </Link>
  );
}

/** 一屏只有一个主按钮（primary，黑底胶囊）；其余是描边按钮。 */
export function Button({
  children,
  onClick,
  disabled,
  primary,
  small,
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  primary?: boolean;
  small?: boolean;
  title?: string;
}) {
  const cls = primary ? `btn${small ? " sm" : ""}` : "ghost";
  return (
    <button type="button" className={cls} onClick={onClick} disabled={disabled} title={title}>
      {children}
    </button>
  );
}

const BADGE: Record<Status, string> = {
  draft: "p-warn",
  approved: "p-acc",
  applying: "p-acc",
  applied: "p-ok",
  undone: "p-gray",
};

export function StatusBadge({ status }: { status: Status }) {
  return <span className={`pill ${BADGE[status]}`}>{STATUS_TEXT[status]}</span>;
}

// ---------------------------------------------------------------- 歌单封面与曲风标签

const OTHER_SUFFIX = " · 其他";

/** 封面配色：「其他」及「电子 · 其他」这类为 0（灰），其余按名字哈希到 1–8。 */
export function coverTone(name: string, otherName: string): number {
  if (name === otherName || name.endsWith(OTHER_SUFFIX)) return 0;
  let sum = 0;
  for (let i = 0; i < name.length; i++) sum += name.charCodeAt(i);
  return 1 + (sum % 8);
}

/** 封面上的字：「电子 · 其他」只写「电子」。 */
export function coverLabel(name: string): string {
  return name.endsWith(OTHER_SUFFIX) ? name.slice(0, -OTHER_SUFFIX.length) : name;
}

export function toneStyle(name: string, otherName: string) {
  const t = coverTone(name, otherName);
  return { background: `var(--t${t})`, color: `var(--i${t})` };
}

export function Cover({
  name,
  count,
  otherName,
  scale = 1,
  onClick,
}: {
  name: string;
  count?: number;
  otherName: string;
  scale?: number;
  onClick?: () => void;
}) {
  const style = { ...toneStyle(name, otherName), ["--fs" as string]: `${Math.round(34 * scale)}px` };
  const body = (
    <>
      <span className="ch">{coverLabel(name)}</span>
      {count !== undefined && <span className="ct">{count} 首</span>}
    </>
  );
  return onClick ? (
    <button type="button" className="cover" style={style} onClick={onClick} aria-label={`查看「${name}」里的歌`}>
      {body}
    </button>
  ) : (
    <div className="cover" style={style}>
      {body}
    </div>
  );
}

export function Mini({ name, otherName }: { name: string; otherName: string }) {
  return (
    <span className="mini" style={toneStyle(name, otherName)} aria-hidden>
      {coverLabel(name).slice(0, 1)}
    </span>
  );
}

export function GenreChips({ names, otherName, max = 6 }: { names: string[]; otherName: string; max?: number }) {
  const shown = names.slice(0, max);
  return (
    <div className="strip">
      {shown.map((n) => (
        <span key={n} style={toneStyle(n, otherName)}>
          {coverLabel(n)}
        </span>
      ))}
      {names.length > max && <span className="p-gray">+{names.length - max}</span>}
    </div>
  );
}

// ---------------------------------------------------------------- 底部操作栏、终端命令

export function BottomBar({ summary, children }: { summary: ReactNode; children: ReactNode }) {
  return (
    <div className="bar">
      <div className="in">
        <div>{summary}</div>
        {children}
      </div>
    </div>
  );
}

export function CopyCommand({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="term">
      <code>{command}</code>
      <button
        type="button"
        onClick={() => {
          void navigator.clipboard.writeText(command).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          });
        }}
      >
        {copied ? "已复制" : "复制命令"}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------- 弹窗（确认、输入名字）

interface DialogOpts {
  title: string;
  body?: ReactNode;
  ok: string;
  cancel?: string;
  input?: { placeholder: string; max?: number }; // 有则弹出输入框，返回输入的文字
}
type Pending = { opts: DialogOpts; resolve: (v: string | null) => void } | null;

let dialog: Pending = null;
const dialogListeners = new Set<() => void>();
function setDialog(d: Pending): void {
  dialog = d;
  dialogListeners.forEach((f) => f());
}
const dialogStore = {
  get: () => dialog,
  subscribe(f: () => void) {
    dialogListeners.add(f);
    return () => dialogListeners.delete(f);
  },
};

function open(opts: DialogOpts): Promise<string | null> {
  dialog?.resolve(null);
  return new Promise((resolve) => setDialog({ opts, resolve }));
}

/** 是/否确认。 */
export const askConfirm = (opts: Omit<DialogOpts, "input">) => open(opts).then((v) => v !== null);

/** 让用户输入一段文字；取消返回 null。 */
export const askText = (opts: DialogOpts & { input: NonNullable<DialogOpts["input"]> }) =>
  open(opts).then((v) => (v === null ? null : v.trim() || null));

export function DialogHost() {
  const d = useSyncExternalStore(dialogStore.subscribe, dialogStore.get);
  const [text, setText] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    setText("");
    if (d?.opts.input) setTimeout(() => input.current?.focus(), 0);
  }, [d]);
  if (!d) return null;
  const close = (v: string | null) => {
    d.resolve(v);
    setDialog(null);
  };
  const ready = !d.opts.input || text.trim() !== "";
  return (
    <div className="scrim" onClick={() => close(null)}>
      <div
        className="modal"
        role="dialog"
        aria-modal
        aria-label={d.opts.title}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.key === "Escape" && close(null)}
      >
        <h2>{d.opts.title}</h2>
        {d.opts.body && <div className="sub">{d.opts.body}</div>}
        {d.opts.input && (
          <input
            ref={input}
            className="field mt-5"
            value={text}
            maxLength={d.opts.input.max}
            placeholder={d.opts.input.placeholder}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ready && close(text)}
          />
        )}
        <div className="mt-6 flex justify-end gap-3">
          <Button onClick={() => close(null)}>{d.opts.cancel ?? "取消"}</Button>
          <Button primary small disabled={!ready} onClick={() => close(d.opts.input ? text : "")}>
            {d.opts.ok}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 轻提示

type Toast = { text: string; tone: "info" | "error"; id: number } | null;
let current: Toast = null;
const toastListeners = new Set<() => void>();
const toastStore = {
  get: () => current,
  subscribe(f: () => void) {
    toastListeners.add(f);
    return () => toastListeners.delete(f);
  },
};

export function toast(text: string, tone: "info" | "error" = "info"): void {
  const id = Date.now();
  current = { text, tone, id };
  toastListeners.forEach((f) => f());
  setTimeout(
    () => {
      if (current?.id !== id) return;
      current = null;
      toastListeners.forEach((f) => f());
    },
    tone === "error" ? 5000 : 2500,
  );
}

export function ToastHost() {
  const t = useSyncExternalStore(toastStore.subscribe, toastStore.get);
  return t ? (
    <div className={`toast${t.tone === "error" ? " error" : ""}`} role="status">
      {t.text}
    </div>
  ) : null;
}

// ---------------------------------------------------------------- 时间

const pad = (n: number) => String(n).padStart(2, "0");

/** 11月1日 */
export function formatDay(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : `${d.getMonth() + 1}月${d.getDate()}日`;
}

/** 今天 14:20 / 昨天 09:05 / 10月30日 12:00 */
export function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const days = Math.round((new Date().setHours(0, 0, 0, 0) - new Date(d).setHours(0, 0, 0, 0)) / 86400000);
  const day = days === 0 ? "今天" : days === 1 ? "昨天" : formatDay(iso);
  return `${day} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
