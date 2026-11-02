import { useCallback, useState } from "react";

import { ApiError, get, post, type EditResult, type PlanState } from "./api";
import { askConfirm, toast } from "./ui";

/** 提交一次修改；done 返回成功后的提示文字（空串表示不提示）。成功返回结果，否则 null。 */
export type RunEdit = (path: string, body: object, done: (r: EditResult) => string) => Promise<EditResult | null>;

/**
 * 修改整理的统一流程：先重新读取状态（终端里可能刚确认、建好或改过），已确认的先问
 * "改动后需要重新确认"，再带上页面看到的内容哈希提交；服务端发现内容已变时拒绝并让页面刷新。
 */
export function useEdit(planId: string, loadedHash: string | undefined, reload: () => Promise<void>) {
  const [busy, setBusy] = useState(false);
  const base = `/api/plans/${encodeURIComponent(planId)}`;

  const run: RunEdit = useCallback(
    async (path, body, done) => {
      setBusy(true);
      try {
        const s = await get<PlanState>(`${base}/state`);
        if (s.content_hash !== loadedHash || !s.editable) {
          await reload();
          toast("这份整理在终端里有更新，已刷新，请再操作一次");
          return null;
        }
        let confirmReset = false;
        if (s.status === "approved") {
          confirmReset = await askConfirm({
            title: "这份整理已经确认过",
            body: "改动后需要在终端里重新确认",
            ok: "继续修改",
          });
          if (!confirmReset) return null;
        }
        const r = await post<EditResult>(`${base}/${path}`, {
          ...body,
          expected_hash: s.content_hash,
          confirm_reset: confirmReset,
        });
        await reload();
        const text = done(r);
        if (text) toast(text);
        return r;
      } catch (e) {
        const err = e as ApiError;
        if (err.status === 0) {
          toast("这次修改没保存上，连上后再试一次", "error");
        } else if (err.code === "stale" || err.code === "not_editable" || err.code === "confirm_reset") {
          await reload();
          toast("这份整理在终端里有更新，已刷新，请再操作一次", "error");
        } else {
          toast(err.message, "error");
        }
        return null;
      } finally {
        setBusy(false);
      }
    },
    [base, loadedHash, reload],
  );
  return { run, busy };
}
