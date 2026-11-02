import { useEffect, useState } from "react";

import { ApiError, post, type Genres, type GroupSpec, type PlanDetail, type Split, type Summary } from "../api";
import { useLoad } from "../hooks";
import { navigate, to } from "../router";
import { fromPlan, toSplit, toToml, useSimulate, type Draft } from "../split";
import { Back, BottomBar, Icon, Mini } from "../ui";
import { usePlan } from "./Result";

/** 自己定怎么分（自定义分组）和更多设置。改动先预估首数，点"用这个分法"才保存。 */
export function SplitEditor({ planId, more }: { planId: string; more: boolean }) {
  const { d, error, editable, edit } = usePlan(planId);
  const genres = useLoad<Genres>(`/api/plans/${encodeURIComponent(planId)}/genres`).data;
  const [draft, setDraft] = useState<Draft | null>(null);
  useEffect(() => {
    if (!d || draft) return;
    const base = fromPlan(d);
    setDraft(
      more || base.preset === "custom"
        ? base
        : { ...base, preset: "custom", groups: [{ name: "", genres: [] }], match: "primary" },
    );
  }, [d, draft, more]);

  if (error && !d) return <div className="wrap small">{error.message}</div>;
  if (!d || !draft || !genres) return null;
  return <Editor plan={d} genres={genres} draft={draft} setDraft={setDraft} editable={editable} edit={edit} />;
}

function Editor({
  plan,
  genres,
  draft,
  setDraft,
  editable,
  edit,
}: {
  plan: PlanDetail;
  genres: Genres;
  draft: Draft;
  setDraft: (d: Draft) => void;
  editable: boolean;
  edit: ReturnType<typeof usePlan>["edit"];
}) {
  const [importError, setImportError] = useState("");
  const patch = (p: Partial<Draft>) => setDraft({ ...draft, ...p });
  const custom = draft.preset === "custom";
  const problem = !custom
    ? ""
    : draft.groups.length === 0
      ? "至少要有一个歌单"
      : draft.groups.some((g) => !g.name?.trim())
        ? "每个歌单都要起个名字"
        : draft.groups.some((g) => g.genres.length === 0)
          ? "每组至少要有一个曲风"
          : "";
  const split = problem ? null : toSplit(draft);
  const sim = useSimulate(plan.id, split, draft.minSize);
  const setGroups = (groups: GroupSpec[]) => patch({ groups });

  const apply = async () => {
    if (!split) return;
    const r = await edit.run(
      "reclassify",
      { split, min_size: draft.minSize.trim() ? Number(draft.minSize) : null },
      (x) => (x.changed ? "已换成新的分法" : "分法已保存，分类结果没有变化"),
    );
    if (r) navigate(to.result(plan.id));
  };
  const importToml = async (file: File) => {
    try {
      const s = await post<Split>("/api/split/parse", { text: await file.text() });
      patch({ preset: "custom", groups: s.groups, match: s.match, other: s.other, topN: s.top_n ? String(s.top_n) : "" });
      setImportError("");
    } catch (e) {
      setImportError((e as ApiError).message);
    }
  };
  const exportToml = () => {
    const url = URL.createObjectURL(new Blob([toToml(draft)], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "tunetidy-groups.toml";
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <>
      <div className="wrap">
        <Back to={to.result(plan.id)}>{plan.sources.map((s) => s.name).join(" + ")}</Back>
        <h1>{custom ? "自己定怎么分" : "更多设置"}</h1>
        {custom && (
          <p className="sub">每一组对应一个新歌单，往里面添加曲风。歌曲会进入匹配到的那一组，都没匹配上的进「{plan.other_name}」</p>
        )}

        <fieldset disabled={!editable}>
          {custom && (
            <>
              {draft.groups.map((g, i) => (
                <GroupCard
                  key={i}
                  group={g}
                  index={i}
                  total={draft.groups.length}
                  count={sim.result?.summary.categories[g.name?.trim() ?? ""]}
                  genres={genres}
                  otherName={plan.other_name}
                  onChange={(next) => setGroups(draft.groups.map((x, j) => (j === i ? next : x)))}
                  onMove={(dir) => {
                    const gs = [...draft.groups];
                    [gs[i], gs[i + dir]] = [gs[i + dir], gs[i]];
                    setGroups(gs);
                  }}
                  onRemove={() => setGroups(draft.groups.filter((_, j) => j !== i))}
                />
              ))}
              <button type="button" className="link mt-4" onClick={() => setGroups([...draft.groups, { name: "", genres: [] }])}>
                + 再加一个歌单
              </button>
            </>
          )}

          <div className="sect">剩下的歌</div>
          <label className="flex items-center gap-3 py-2">
            <span className="flex-1">给剩下的歌建一个「{plan.other_name}」歌单</span>
            <button
              type="button"
              role="switch"
              aria-checked={draft.other}
              className={`toggle${draft.other ? " on" : ""}`}
              onClick={() => patch({ other: !draft.other })}
            />
          </label>
          {!draft.other && <p className="small">关掉的话，这些歌不放进任何新歌单</p>}

          <div className="sect">更多设置</div>
          <label className="flex items-center gap-3 py-2">
            <span className="flex-1">太小的歌单并进「{plan.other_name}」：不到</span>
            <input className="num" type="number" min={1} value={draft.minSize} onChange={(e) => patch({ minSize: e.target.value })} />
            首
          </label>
          <label className="flex items-center gap-3 py-2">
            <span className="flex-1">最多建几个歌单（不算「{plan.other_name}」，空着表示不限）</span>
            <input
              className="num"
              type="number"
              min={1}
              value={draft.topN}
              placeholder="不限"
              onChange={(e) => patch({ topN: e.target.value })}
            />
          </label>
          {custom && (
            <div className="flex flex-wrap items-center gap-3 py-2">
              <span className="flex-1">哪些歌算命中</span>
              <div className="seg" role="radiogroup" aria-label="哪些歌算命中">
                {(
                  [
                    ["primary", "看主要曲风"],
                    ["any", "带标签就算"],
                  ] as const
                ).map(([m, label]) => (
                  <button
                    key={m}
                    type="button"
                    role="radio"
                    aria-checked={draft.match === m}
                    className={draft.match === m ? "on" : ""}
                    onClick={() => patch({ match: m })}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          )}
        </fieldset>

        {custom && (
          <div className="small mt-8 flex flex-wrap items-center gap-4">
            {editable && (
              <label className="link cursor-pointer">
                从文件导入
                <input
                  type="file"
                  accept=".toml,text/plain"
                  className="hidden"
                  onChange={(e) => e.target.files?.[0] && void importToml(e.target.files[0])}
                />
              </label>
            )}
            <button type="button" className="link" disabled={draft.groups.length === 0} onClick={exportToml}>
              导出成文件
            </button>
            {importError && <span className="text-danger">{importError}</span>}
          </div>
        )}
      </div>

      {editable && (
        <BottomBar summary={<Estimate problem={problem} error={sim.error} summary={sim.result?.summary} otherName={plan.other_name} />}>
          <button type="button" className="btn" disabled={!sim.result || edit.busy} onClick={() => void apply()}>
            用这个分法
          </button>
        </BottomBar>
      )}
    </>
  );
}

function Estimate({
  problem,
  error,
  summary,
  otherName,
}: {
  problem: string;
  error: string | null;
  summary?: Summary;
  otherName: string;
}) {
  if (problem) return <span className="text-warn">{problem}</span>;
  if (error) return <span className="text-danger">{error}</span>;
  if (!summary) return <span className="small">正在估算…</span>;
  const cats = Object.entries(summary.categories).sort(([a], [b]) => Number(a === otherName) - Number(b === otherName));
  return (
    <>
      <b>将新建 {cats.length} 个歌单</b>
      <div className="small truncate">
        {cats.map(([k, v]) => `${k} ${v}`).join(" · ")}
        {summary.pending > 0 && ` · ${summary.pending} 首没分好`}
      </div>
    </>
  );
}

function GroupCard({
  group,
  index,
  total,
  count,
  genres,
  otherName,
  onChange,
  onMove,
  onRemove,
}: {
  group: GroupSpec;
  index: number;
  total: number;
  count: number | undefined;
  genres: Genres;
  otherName: string;
  onChange: (g: GroupSpec) => void;
  onMove: (dir: -1 | 1) => void;
  onRemove: () => void;
}) {
  const name = group.name ?? "";
  return (
    <div className="grp">
      <div className="flex items-center gap-3">
        <Mini name={name || "?"} otherName={otherName} />
        <input
          className="gn min-w-0 flex-1"
          value={name}
          maxLength={40}
          placeholder="歌单名，比如：华语"
          aria-label={`第 ${index + 1} 个歌单的名字`}
          onChange={(e) => onChange({ ...group, name: e.target.value })}
        />
        <span className="cnt shrink-0">{count !== undefined ? `${count} 首` : ""}</span>
      </div>
      <div>
        {group.genres.map((x) => (
          <span key={x} className="tag">
            {x}
            <button type="button" aria-label={`去掉 ${x}`} onClick={() => onChange({ ...group, genres: group.genres.filter((y) => y !== x) })}>
              <Icon name="x" size={12} />
            </button>
          </span>
        ))}
        <select
          className="tag cursor-pointer border-none"
          value=""
          aria-label="加曲风"
          onChange={(e) => e.target.value && !group.genres.includes(e.target.value) && onChange({ ...group, genres: [...group.genres, e.target.value] })}
        >
          <option value="">+ 加曲风</option>
          <optgroup label="大类">
            {genres.level1.map((g) => (
              <option key={g.name} value={g.name}>
                {g.name}（{g.count}）
              </option>
            ))}
          </optgroup>
          <optgroup label="细类">
            {genres.level2.map((g) => (
              <option key={g.name} value={g.name}>
                {g.name}（{g.count}）
              </option>
            ))}
          </optgroup>
        </select>
      </div>
      <div className="small mt-3 flex gap-4">
        <button type="button" className="link" disabled={index === 0} onClick={() => onMove(-1)}>
          上移
        </button>
        <button type="button" className="link" disabled={index === total - 1} onClick={() => onMove(1)}>
          下移
        </button>
        <button type="button" className="ml-auto text-danger" onClick={onRemove}>
          删掉这个歌单
        </button>
      </div>
    </div>
  );
}
