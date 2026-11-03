// 本地服务接口。访问口令由 tunetidy ui 放在网址的 #token= 里（不会发给服务器、不进日志），
// 读到后存进本标签页的 sessionStorage，并从地址栏去掉。

const TOKEN_KEY = "tunetidy-token";

function readToken(): string | null {
  const m = location.hash.match(/token=([^&]+)/);
  try {
    if (m) {
      sessionStorage.setItem(TOKEN_KEY, m[1]);
      history.replaceState(null, "", location.pathname + location.search);
      return m[1];
    }
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return m ? m[1] : null;
  }
}

export const token = readToken();

// ---------------------------------------------------------------- 连接状态（断线遮罩用）

/** ok：正常；down：连不上本地服务；token：口令失效（服务重启过，换了口令）。 */
export type Conn = "ok" | "down" | "token";
let conn: Conn = "ok";
const connListeners = new Set<() => void>();

function setConn(c: Conn): void {
  if (c === conn) return;
  conn = c;
  connListeners.forEach((f) => f());
}

export const connection = {
  get: (): Conn => conn,
  subscribe(f: () => void): () => void {
    connListeners.add(f);
    return () => connListeners.delete(f);
  },
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
    readonly retryAfter?: number, // 锁定或限频时，还要等多少秒
  ) {
    super(message);
  }
}

async function request<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "X-TuneTidy-Token": token ?? "" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let r: Response;
  try {
    r = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    setConn("down");
    throw new ApiError("连不上理音了", 0);
  }
  setConn(r.status === 401 ? "token" : "ok");
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = typeof data.detail === "string" ? data.detail : `请求失败（${r.status}）`;
    throw new ApiError(detail, r.status, data.code, data.retry_after);
  }
  return data as T;
}

export const get = <T>(path: string) => request<T>("GET", path);
export const post = <T>(path: string, body: unknown) => request<T>("POST", path, body);

// ---------------------------------------------------------------- 数据类型（与 server/api.py 对应）

export type Status = "draft" | "approved" | "applying" | "applied" | "undone";

export const STATUS_TEXT: Record<Status, string> = {
  draft: "还没建歌单",
  approved: "已确认，等待创建",
  applying: "正在创建",
  applied: "已建好",
  undone: "已撤销",
};

export interface CategoryCount {
  name: string;
  count: number;
}

export interface PlanRow {
  id: string;
  created_at: string;
  updated_at: string;
  status: Status;
  sources: string[];
  source_ids: string[];
  split: Split;
  other_name: string;
  total: number;
  pending: number;
  categories: CategoryCount[]; // 将新建的歌单，顺序同写回（「其他」最后）
}

export interface PlanState {
  status: Status;
  content_hash: string;
  approved: boolean;
  editable: boolean;
}

export interface Summary {
  total: number;
  assigned: number;
  pending: number;
  unavailable: number;
  ungrouped: number;
  categories: Record<string, number>;
}

export interface GroupSpec {
  name?: string | null;
  genres: string[];
}

export interface Split {
  level: 1 | 2;
  groups: GroupSpec[];
  match: "primary" | "any";
  top_n: number | null;
  other: boolean;
}

export interface PlanDetail extends PlanState {
  id: string;
  created_at: string;
  sources: { id: string; name: string }[];
  split: Split;
  min_size: number;
  other_name: string;
  summary: Summary;
  manual: number;
  private: boolean;
  playlists: PlannedPlaylist[];
}

export interface PlannedPlaylist {
  category: string;
  name: string;
  default_name: string;
  count: number;
}

export type SongStatus = "assigned" | "pending" | "unavailable" | "ungrouped";

export interface SongRow {
  position: number;
  platform: string;
  id: string;
  name: string;
  artists: string[];
  album: string;
  status: SongStatus;
  status_text: string;
  category: string | null;
  suggested: string | null;
  confidence: number;
  reason: string; // 完整依据（悬停显示）
  why: string; // 一句话原因（"没分好的歌"页用）
  genres: { name: string; used: boolean }[]; // 网易云给的曲风标签；used 为假：分类时没用上
  tied: string[]; // 并列得分最高的分类
  unsure: boolean; // 工具拿不准的歌（"没分好的歌"页列出，改过之后也留着）
  manual: boolean;
  playlist: string | null;
}

export interface Preview extends PlanState {
  private: boolean;
  playlists: { category: string; name: string; count: number }[];
  total: number;
  skipped: { pending: number; unavailable: number; ungrouped: number };
  commands: string[];
}

export interface Genre {
  name: string; // 一级名或"一级-二级"路径
  label: string; // 按它分出的分类名
  count: number; // 带这个标签（含下级）的歌数
}

export interface Genres {
  level1: Genre[];
  level2: Genre[];
}

export interface EditResult extends PlanState {
  changed: boolean;
  approval_reset: boolean;
  manual: number;
  summary: Summary;
}

// ---------------------------------------------------------------- 网页确认、建歌单、历史与撤销

export interface ConfirmStatus {
  available: boolean; // 理音是在交互式终端里启动的：确认码会显示在那个终端里
  locked_seconds: number; // 试错太多次后的锁定剩余秒数，0 表示没锁定
}

/** 申请确认码的回执：不含码本身，码只显示在启动理音的终端里。 */
export interface Ticket {
  action?: "apply" | "resume";
  summary: string[]; // 这次确认要做什么
  expires_in: number;
  attempts_left: number;
}

export interface PlaylistProgress {
  name: string;
  added: number;
  total: number;
  status: string; // 等待中 / 添加中 / 已完成
}

export interface JobSummary {
  job_id: string;
  kind: "analyze" | "apply" | "undo";
  status: "running" | "done" | "failed" | "interrupted";
  phase: string | null;
  done: number;
  total: number;
  plan_id: string | null;
  run_id: string | null;
  message?: string;
  error?: string;
  playlists?: PlaylistProgress[];
  created?: { name: string; playlist_id: string | null; count: number }[];
  unaddable?: number;
  deleted?: string[];
  trimmed?: { name: string; removed: number }[];
  skipped?: string[];
  warnings?: string[];
  // 分析任务的读取进度
  sources?: string[];
  cached?: number; // 以前读过、不用联网的首数（done、total 只计联网读取的歌）
  categories?: { name: string; count: number }[];
  pending?: number;
  eta_seconds?: number | null;
}

export interface RunRow {
  run_id: string;
  plan_id: string;
  created_at: string;
  status: "running" | "interrupted" | "done" | "undoing" | "undone";
  sources: string[];
  split: Split;
  other_name: string;
  playlists: { name: string; category: string; added: number }[];
}

export interface UndoPreview {
  run_id: string;
  created_at: string;
  items: {
    name: string;
    category: string;
    action: "delete" | "remove_ours" | "skip";
    remove: number;
    differences: string[];
    note: string | null;
  }[];
  nothing_to_do: boolean;
  fingerprint: string;
  summary: string[];
}

// ---------------------------------------------------------------- 登录与开始整理

export interface Me {
  logged_in: boolean;
  nickname: string | null;
}

export type LoginStatus = "idle" | "waiting" | "scanned" | "done" | "expired" | "failed";

export interface LoginState {
  status: LoginStatus;
  qr: string[] | null; // 二维码点阵，每行由 0 和 1 组成，1 是黑格
  nickname: string | null;
  error: string | null;
}

export interface PlaylistRow {
  id: string;
  name: string;
  track_count: number;
  owned: boolean; // 自己创建的；否则是收藏的
}

/** 同一来源上次建好、还没撤销的那批（当前账号建的）。 */
export interface PreviousBatch {
  run_id: string;
  created_at: string;
  sources: string[];
  playlists: { name: string; added: number }[];
}
