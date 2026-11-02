// 拆分方式（界面上叫"拆法"）：网页里的草稿 ⇄ 接口里的 Split，以及按草稿预估首数。

import { useEffect, useState } from "react";

import { ApiError, post, type PlanDetail, type GroupSpec, type Split, type Summary } from "./api";

export type Preset = "level1" | "level2" | "only" | "custom";

export interface Draft {
  preset: Preset;
  only: string; // "只分一类"的曲风（一级名或"一级-二级"路径）
  groups: GroupSpec[];
  match: Split["match"];
  topN: string; // 空：不限
  minSize: string;
  other: boolean;
}

export function presetOf(s: Split): Preset {
  const groups = s.groups ?? [];
  if (groups.length === 0) return s.level === 2 ? "level2" : "level1";
  return groups.length === 1 && !groups[0].name ? "only" : "custom";
}

export function fromPlan(d: PlanDetail): Draft {
  const s = d.split;
  const preset = presetOf(s);
  const groups = s.groups ?? [];
  return {
    preset,
    only: preset === "only" ? groups[0].genres[0] : "",
    groups: preset === "only" ? [] : groups,
    match: s.match ?? "primary",
    topN: s.top_n ? String(s.top_n) : "",
    minSize: String(d.min_size),
    other: s.other ?? true,
  };
}

export function toSplit(d: Draft, match: Split["match"] = d.match): Split {
  const groups =
    d.preset === "only" ? (d.only ? [{ name: null, genres: [d.only] }] : []) : d.preset === "custom" ? d.groups : [];
  return {
    level: d.preset === "level2" ? 2 : 1,
    groups,
    match: d.preset === "only" || d.preset === "custom" ? match : "primary",
    top_n: d.topN.trim() ? Number(d.topN) : null,
    other: d.other,
  };
}

export function toToml(d: Draft): string {
  const q = (s: string) => JSON.stringify(s); // JSON 字符串转义与 TOML 基本字符串兼容
  const lines = [`match = ${q(d.match)}`, `other = ${d.other}`];
  if (d.topN.trim()) lines.push(`top_n = ${Number(d.topN)}`);
  for (const g of d.groups) {
    lines.push("", "[[groups]]", `name = ${q(g.name ?? "")}`, `genres = [${g.genres.map(q).join(", ")}]`);
  }
  return lines.join("\n") + "\n";
}

/** 首页、以前的整理里用的一句话拆法：按大类 / 按细类 / 只分一类（流行）/ 自定义（3 组）。 */
export function describeShort(s: Split): string {
  switch (presetOf(s)) {
    case "level1":
      return "按大类";
    case "level2":
      return "按细类";
    case "only":
      return `只分一类（${s.groups[0].genres[0]}）`;
    case "custom":
      return `自定义（${s.groups.length} 组）`;
  }
}

/** 按草稿预估各分类首数（只用快照，不保存）；split 为 null 时不预估。 */
export function useSimulate(planId: string, split: Split | null, minSize: string) {
  const [result, setResult] = useState<{ summary: Summary; changed: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const body = split ? JSON.stringify({ split, min_size: minSize.trim() ? Number(minSize) : null }) : null;
  useEffect(() => {
    if (!body) {
      setResult(null);
      return;
    }
    let live = true;
    const t = setTimeout(async () => {
      try {
        const r = await post<{ summary: Summary; changed: boolean }>(
          `/api/plans/${encodeURIComponent(planId)}/simulate`,
          JSON.parse(body),
        );
        if (live) {
          setResult(r);
          setError(null);
        }
      } catch (e) {
        if (live) {
          setResult(null);
          setError((e as ApiError).message);
        }
      }
    }, 250);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [planId, body]);
  return { result, error };
}
