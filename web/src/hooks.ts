import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { ApiError, get, type Me, type PlanState } from "./api";

const POLL_MS = 5000;

/** 读取一次数据；reload() 重新读取。 */
export function useLoad<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const reload = useCallback(async () => {
    try {
      setData(await get<T>(path));
      setError(null);
    } catch (e) {
      setError(e as ApiError);
    }
  }, [path]);
  useEffect(() => {
    void reload();
  }, [reload]);
  return { data, error, reload, setData };
}

/**
 * 定时读取方案状态，窗口重新获得焦点时也读一次：CLI 可能在网页打开期间批准、执行
 * 或改动了方案。内容哈希与页面已加载的不一致时调用 onChanged。
 */
export function usePlanState(planId: string, loadedHash: string | undefined, onChanged: () => void) {
  const [state, setState] = useState<PlanState | null>(null);
  const changed = useRef(onChanged);
  changed.current = onChanged;
  const hash = useRef(loadedHash);
  hash.current = loadedHash;

  // notify=false：页面自己刚改完方案后重读状态，内容变化是预期的，不算"别处修改"
  const check = useCallback(async (notify: unknown = true) => {
    try {
      const s = await get<PlanState>(`/api/plans/${encodeURIComponent(planId)}/state`);
      setState(s);
      if (notify !== false && hash.current && s.content_hash !== hash.current) changed.current();
    } catch {
      // 服务暂时不可用时保持原状，下次再读
    }
  }, [planId]);

  useEffect(() => {
    void check();
    const t = setInterval(check, POLL_MS);
    window.addEventListener("focus", check);
    return () => {
      clearInterval(t);
      window.removeEventListener("focus", check);
    };
  }, [check]);
  return { state, check };
}

// ---------------------------------------------------------------- 当前网易云账号

let me: Me | null = null;
let meLoading: Promise<void> | null = null;
const meListeners = new Set<() => void>();

/** 重新读取登录状态（登录成功后调用），导航底部的昵称随之更新。 */
export function refreshMe(): Promise<void> {
  meLoading = get<Me>("/api/me")
    .then((m) => {
      me = m;
      meListeners.forEach((f) => f());
    })
    .catch(() => undefined);
  return meLoading;
}

/** 当前登录的网易云账号；还没读到时为 null。整个页面只读一次，登录后 refreshMe() 刷新。 */
export function useMe(): Me | null {
  const value = useSyncExternalStore(
    (f) => {
      meListeners.add(f);
      return () => meListeners.delete(f);
    },
    () => me,
  );
  useEffect(() => {
    if (!meLoading) void refreshMe();
  }, []);
  return value;
}
