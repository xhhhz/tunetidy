import type { PreviousBatch as Prev } from "../api";
import { useLoad } from "../hooks";
import { navigate, to } from "../router";
import { formatDay } from "../ui";

/**
 * 新歌单建好后，同一来源上次建的那批还在时，提示可以撤销它。点了只是进入撤销页：
 * 那里先核对歌单现状，再单独输入确认码。
 */
export function PreviousBatch({ planId, compact = false }: { planId: string; compact?: boolean }) {
  const prev = useLoad<Prev | null>(`/api/plans/${encodeURIComponent(planId)}/previous`).data;
  if (!prev) return null;
  const when = formatDay(prev.created_at);
  const n = prev.playlists.length;
  const go = () => navigate(to.undo(prev.run_id));
  if (compact) {
    return (
      <div className="small">
        上次那批（{when}，{n} 个歌单）还在 ·{" "}
        <button type="button" className="link" onClick={go}>
          去撤销
        </button>
      </div>
    );
  }
  return (
    <div className="banner text-left">
      <div className="t">
        <b>要撤销上次那批吗？</b>
        <div className="small">
          {when}从{prev.sources.map((s) => `「${s}」`).join(" + ")}建的 {n} 个歌单。只删理音建的，你自己的歌单不会动
        </div>
      </div>
      <button type="button" className="ghost" onClick={go}>
        去撤销
      </button>
    </div>
  );
}
