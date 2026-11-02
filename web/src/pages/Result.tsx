import { useCallback, useEffect, useRef, useState } from "react";

import type { Genres, PlanDetail, PlanRow, Split } from "../api";
import { useEdit, type RunEdit } from "../edit";
import { useLoad, usePlanState } from "../hooks";
import { navigate, to } from "../router";
import { describeShort, presetOf, useSimulate, type Preset } from "../split";
import { Back, BottomBar, CopyCommand, Cover, Icon, StatusBadge, formatWhen, toast } from "../ui";
import { sourceKey } from "./Home";

const PRESETS: [Preset, string][] = [
  ["only", "只分一类"],
  ["level1", "按大类"],
  ["level2", "按细类"],
  ["custom", "自定义"],
];

/** 一份整理的数据、状态轮询和修改流程；结果页、歌曲页、没分好的歌页共用。 */
export function usePlan(planId: string) {
  const id = encodeURIComponent(planId);
  const detail = useLoad<PlanDetail>(`/api/plans/${id}`);
  const reloadDetail = detail.reload;
  // 页面自己的其他数据（例如歌曲列表），随整理一起重读
  const extra = useRef<(() => Promise<void>) | null>(null);
  const { state, check } = usePlanState(planId, detail.data?.content_hash, () => {
    void reloadDetail();
    void extra.current?.();
    toast("这份整理在终端里有更新，已刷新");
  });
  const reload = useCallback(async () => {
    await Promise.all([reloadDetail(), check(false), extra.current?.()]);
  }, [reloadDetail, check]);
  const edit = useEdit(planId, detail.data?.content_hash, reload);
  const d = detail.data;
  const status = state?.status ?? d?.status;
  const editable = state?.editable ?? d?.editable ?? false;
  return { d, error: detail.error, status, editable, edit, extra };
}

/** 整理结果（核心页）：拆法切换、将要新建的歌单、没分好的提示。 */
export function Result({ planId }: { planId: string }) {
  const { d, error, status, editable, edit } = usePlan(planId);
  const genres = useLoad<Genres>(`/api/plans/${encodeURIComponent(planId)}/genres`).data;
  if (error && !d) return <div className="wrap small">{error.message}</div>;
  if (!d || !status) return null;

  const total = d.playlists.reduce((n, p) => n + p.count, 0);
  const n = d.playlists.length;
  const preset = presetOf(d.split);
  const readOnly = !editable;

  const apply = (split: Split) =>
    void edit.run("reclassify", { split, min_size: null }, (r) => (r.changed ? "" : "分类结果没有变化"));
  const keep = { top_n: d.split.top_n ?? null, other: d.split.other ?? true };
  const pick = (p: Preset) => {
    if (p === preset && p !== "custom") return;
    if (p === "custom") return navigate(to.split(planId, false));
    if (p === "only") {
      const x = genres?.level1[0]?.name;
      if (x) apply({ level: 1, groups: [{ name: null, genres: [x] }], match: "primary", ...keep });
      return;
    }
    apply({ level: p === "level2" ? 2 : 1, groups: [], match: "primary", ...keep });
  };

  return (
    <>
      <div className="wrap">
        <Back to={to.home()}>整理</Back>
        <div className="flex items-start gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-3">
              <h1>{d.sources.map((s) => s.name).join(" + ")}</h1>
              <StatusBadge status={status} />
            </div>
            <p className="sub">
              {d.summary.total} 首 · {formatWhen(d.created_at)} 读取
            </p>
          </div>
          <Earlier plan={d} />
        </div>

        {readOnly && <ReadOnlyBanner plan={d} status={status} count={n} />}

        <div className="mt-7 flex flex-wrap items-center gap-4">
          <div className={`seg${readOnly || edit.busy ? " off" : ""}`} role="radiogroup" aria-label="拆法">
            {PRESETS.map(([p, label]) => (
              <button
                key={p}
                type="button"
                role="radio"
                aria-checked={p === preset}
                className={p === preset ? "on" : ""}
                onClick={() => pick(p)}
              >
                {label}
              </button>
            ))}
          </div>
          {!readOnly && (
            <button type="button" className="link text-sm" onClick={() => navigate(to.split(planId, preset !== "custom"))}>
              {preset === "custom" ? "编辑分组" : "更多设置"}
            </button>
          )}
        </div>

        {preset === "only" && genres && (
          <OnlyOne plan={d} genres={genres} disabled={readOnly || edit.busy} apply={apply} />
        )}

        {n === 0 ? (
          <p className="sub mt-8">按现在的分法，没有歌能放进新歌单</p>
        ) : (
          <div className="tiles">
            {d.playlists.map((p) => (
              <div key={p.category} className="tile">
                <Cover
                  name={p.category}
                  count={p.count}
                  otherName={d.other_name}
                  scale={0.9}
                  onClick={() => navigate(to.songs(planId, { playlist: p.category }))}
                />
                <PlaylistName plan={d} category={p.category} editable={!readOnly} run={edit.run} />
              </div>
            ))}
          </div>
        )}

        {!readOnly &&
          (d.summary.pending > 0 ? (
            <button type="button" className="callout" onClick={() => navigate(to.pending(planId))}>
              <b>{d.summary.pending} 首没分好</b>
              <span className="text-sm">不管它们的话，不会放进新歌单</span>
              <span className="go">去分 ›</span>
            </button>
          ) : (
            <div className="callout ok">
              <Icon name="check" size={16} />
              <b>全部分好了</b>
            </div>
          ))}
        <div className="quiet">
          {d.summary.unavailable > 0 && <span>另有 {d.summary.unavailable} 首加不进去（已下架或没版权）</span>}
          {d.summary.ungrouped > 0 && <span>{d.summary.ungrouped} 首不放进新歌单</span>}
          <button type="button" className="link" onClick={() => navigate(to.songs(planId, { filter: "all" }))}>
            看全部歌曲
          </button>
        </div>
      </div>

      {!readOnly && (
        <BottomBar
          summary={
            <>
              <b>
                将新建 {n} 个{d.private ? "私密" : ""}歌单
              </b>
              <div className="small">共 {total} 首 · 名字点一下就能改</div>
            </>
          }
        >
          <button type="button" className="btn" disabled={n === 0} onClick={() => navigate(to.confirm(planId))}>
            下一步
          </button>
        </BottomBar>
      )}
    </>
  );
}

/** "只分一类"：选曲风，并排两种算法各自的首数。 */
function OnlyOne({
  plan,
  genres,
  disabled,
  apply,
}: {
  plan: PlanDetail;
  genres: Genres;
  disabled: boolean;
  apply: (s: Split) => void;
}) {
  const x = plan.split.groups[0]?.genres[0] ?? "";
  const all = [...genres.level1, ...genres.level2];
  const label = all.find((g) => g.name === x)?.label ?? x;
  const options = genres.level1.some((g) => g.name === x) ? genres.level1 : [...genres.level1, ...all.filter((g) => g.name === x)];
  const keep = { level: 1 as const, top_n: plan.split.top_n ?? null, other: plan.split.other ?? true };
  const split = (genre: string, match: Split["match"]): Split => ({
    ...keep,
    groups: [{ name: null, genres: [genre] }],
    match,
  });
  const min = String(plan.min_size);
  const primary = useSimulate(plan.id, split(x, "primary"), min);
  const any = useSimulate(plan.id, split(x, "any"), min);
  const count = (r: typeof primary) => (r.result ? (r.result.summary.categories[label] ?? 0) : "…");
  const match = plan.split.match ?? "primary";

  return (
    <div className="mt-6">
      <p className="flex flex-wrap items-center gap-2">
        分出
        <select
          className="sel"
          value={x}
          disabled={disabled}
          aria-label="分出哪一类"
          onChange={(e) => apply(split(e.target.value, match))}
        >
          {options.map((g) => (
            <option key={g.name} value={g.name}>
              {g.label}
            </option>
          ))}
        </select>
        ，剩下的放进「{plan.other_name}」。哪些歌算{label}？
      </p>
      <div className="mt-3 flex flex-wrap gap-3">
        {(
          [
            ["primary", `主要是${label}的歌`, `一首歌最主要的曲风是${label}才算`, primary],
            ["any", `带${label}标签的都算`, `比如「R&B、${label}」也会进${label}`, any],
          ] as const
        ).map(([m, title, hint, sim]) => (
          <button
            key={m}
            type="button"
            className={`opt${match === m ? " on" : ""}`}
            disabled={disabled}
            aria-pressed={match === m}
            onClick={() => match !== m && apply(split(x, m))}
          >
            <b>{count(sim)} 首</b>
            <div className="font-semibold">{title}</div>
            <div className="small">{hint}</div>
          </button>
        ))}
      </div>
    </div>
  );
}

/** 封面下的歌单名：点一下就能改，回车或离开输入框时保存，Esc 放弃。 */
function PlaylistName({
  plan,
  category,
  editable,
  run,
}: {
  plan: PlanDetail;
  category: string;
  editable: boolean;
  run: RunEdit;
}) {
  const saved = plan.playlists.find((p) => p.category === category)!.name;
  const [value, setValue] = useState(saved);
  const [error, setError] = useState("");
  useEffect(() => setValue(saved), [saved]);
  if (!editable) return <div className="nm text-sm font-semibold">{saved}</div>;

  const save = async () => {
    const name = value.trim();
    if (name === saved) return setValue(saved);
    const defaultName = plan.playlists.find((p) => p.category === category)!.default_name;
    const effective = name || defaultName;
    if (plan.playlists.some((p) => p.category !== category && p.name === effective)) {
      return setError("已经有一个同名歌单了");
    }
    setError("");
    const r = await run("rename", { category, name }, () => "");
    if (!r) setValue(saved);
  };
  return (
    <div>
      <label className="nm">
        <textarea
          rows={2}
          maxLength={40}
          value={value}
          aria-label={`「${category}」歌单名`}
          onChange={(e) => {
            setValue(e.target.value.replace(/\n/g, ""));
            setError("");
          }}
          onBlur={() => void save()}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              e.currentTarget.blur();
            } else if (e.key === "Escape") {
              setValue(saved);
              setError("");
              setTimeout(() => (e.target as HTMLTextAreaElement).blur(), 0);
            }
          }}
        />
        <Icon name="pen" />
      </label>
      {error && <div className="text-xs text-danger">{error}</div>}
    </div>
  );
}

/** "以前的整理"：同一来源的其他几份。 */
function Earlier({ plan }: { plan: PlanDetail }) {
  const rows = useLoad<PlanRow[]>("/api/plans").data;
  const [open, setOpen] = useState(false);
  if (!rows) return null;
  const me = [...plan.sources.map((s) => s.id)].sort().join("\u0000");
  const same = rows.filter((r) => sourceKey(r) === me);
  if (same.length < 2) return null;
  return (
    <div className="relative shrink-0">
      <button type="button" className="ghost" onClick={() => setOpen(!open)} aria-expanded={open}>
        以前的整理（{same.length - 1}）
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="pop">
            {same.map((r) => (
              <button
                key={r.id}
                type="button"
                className="li"
                onClick={() => {
                  setOpen(false);
                  if (r.id !== plan.id) navigate(to.result(r.id));
                }}
              >
                <span className="t">
                  <b>{describeShort(r.split)}</b>
                  <span>
                    {formatWhen(r.updated_at)} · {r.categories.length} 个歌单
                  </span>
                </span>
                {r.id === plan.id ? <span className="pill p-acc">正在看</span> : <StatusBadge status={r.status} />}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/** 已建好、正在创建、已撤销：只读横幅。v1.0 的撤销和重新整理都在终端里做。 */
function ReadOnlyBanner({ plan, status, count }: { plan: PlanDetail; status: string; count: number }) {
  const analyze = `tunetidy analyze ${plan.sources.map((s) => s.id).join(" ")}`;
  return (
    <div className="banner">
      <div className="t">
        {status === "applied" && (
          <>
            <b>这 {count} 个歌单已经在你的网易云里了</b>
            <div className="small">建好后内容不能再改，想换种拆法可以重新整理，会另建一批新歌单</div>
          </>
        )}
        {status === "applying" && (
          <>
            <b>正在创建</b>
            <div className="small">正在终端里创建，完成后这里会自动更新</div>
          </>
        )}
        {status === "undone" && (
          <>
            <b>已撤销</b>
            <div className="small">这次整理建的歌单已撤销，想再建可以重新整理</div>
          </>
        )}
      </div>
      {status !== "applying" && (
        <div className="flex w-full flex-col gap-2">
          {status === "applied" && (
            <>
              <div className="small">撤销</div>
              <CopyCommand command={`tunetidy undo ${plan.id}`} />
            </>
          )}
          <div className="small">重新整理</div>
          <CopyCommand command={analyze} />
        </div>
      )}
    </div>
  );
}
