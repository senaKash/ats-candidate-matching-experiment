#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Smart pair generator for vacancy-resume annotation.

How it works:
- Reads processed vacancies.csv and resumes.csv
- Computes a rough relevance score for each vacancy-resume pair
- Selects a balanced set of candidates per vacancy:
  * top matches
  * borderline matches
  * random low-score negatives
- Writes labels template ready for manual annotation

Run:
    python build_smart_labels.py

Optional:
    python build_smart_labels.py --config smart_labels_config.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import pandas as pd


DEFAULT_CONFIG = {
    "project_dir": ".",
    "vacancies_csv": "processed/vacancies.csv",
    "resumes_csv": "processed/resumes.csv",
    "annotations_dir": "annotations",
    "reports_dir": "reports",
    "output_labels_file": "labels_template_smart.csv",
    "output_report_file": "pair_selection_report.csv",
    "pairs_per_vacancy": 20,
    "top_k": 8,
    "borderline_k": 6,
    "random_k": 6,
    "random_seed": 42,
    "min_token_len": 2
}

STOPWORDS = {
    "and", "or", "the", "a", "an", "to", "for", "of", "in", "on", "with", "by", "as",
    "at", "is", "are", "be", "from", "this", "that", "will", "can", "we", "our",
    "using", "used", "into", "about", "your", "you", "they", "their",
    "developer", "development", "engineer", "engineering", "software", "application",
    "systems", "system", "work", "working", "experience", "years", "year", "skills",
    "knowledge", "strong", "good", "plus", "required", "preferred"
}


@dataclass
class PairScore:
    vacancy_id: str
    resume_id: str
    score: float
    skill_overlap: float
    text_overlap: float
    exp_score: float
    meta_score: float


def load_config(config_path: Path) -> Dict:
    if config_path.exists():
        user_cfg = json.loads(config_path.read_text(encoding="utf-8"))
        cfg = DEFAULT_CONFIG.copy()
        cfg.update(user_cfg)
        return cfg
    return DEFAULT_CONFIG.copy()


def normalize_text(text: object) -> str:
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    text = str(text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def split_skills(value: object) -> List[str]:
    text = normalize_text(value)
    if not text:
        return []
    parts = re.split(r"[;,|/]+", text)
    out = []
    for p in parts:
        p = normalize_text(p).lower()
        if p:
            out.append(p)
    return sorted(set(out))


def tokenize(text: object, min_len: int = 2) -> Set[str]:
    text = normalize_text(text).lower()
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9\.\+#-]*", text)
    return {
        t for t in tokens
        if len(t) >= min_len and t not in STOPWORDS and not t.isdigit()
    }


def safe_float(value: object) -> Optional[float]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    txt = normalize_text(value)
    if not txt:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", txt)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def overlap_ratio(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / max(1, len(a))


def title_position_bonus(vac_title: str, resume_title: str, last_position: str, specialization: str) -> float:
    v = tokenize(vac_title)
    r = tokenize(" ".join([resume_title, last_position, specialization]))
    if not v or not r:
        return 0.0
    return min(1.0, len(v & r) / max(1, len(v)))


def domain_bonus(v_domain: object, r_domain: object, specialization: object) -> float:
    vd = normalize_text(v_domain).lower()
    rd = normalize_text(r_domain).lower()
    sp = normalize_text(specialization).lower()

    if not vd:
        return 0.0
    if vd == rd:
        return 1.0
    if vd and vd in sp:
        return 0.8
    if rd and (rd in vd or vd in rd):
        return 0.7
    return 0.0


def seniority_bonus(v_seniority: object, total_exp: Optional[float]) -> float:
    s = normalize_text(v_seniority).lower()
    if not s or total_exp is None:
        return 0.5
    if "junior" in s:
        return 1.0 if total_exp <= 3 else 0.7
    if "middle" in s:
        return 1.0 if 2 <= total_exp <= 6 else 0.6
    if "senior" in s:
        return 1.0 if total_exp >= 5 else 0.3
    return 0.5


def experience_score(min_exp: object, total_exp: object) -> float:
    vmin = safe_float(min_exp)
    rexp = safe_float(total_exp)
    if vmin is None and rexp is None:
        return 0.5
    if vmin is None:
        return 0.7
    if rexp is None:
        return 0.4

    if rexp >= vmin:
        margin = rexp - vmin
        return min(1.0, 0.8 + min(margin, 4.0) * 0.05)
    gap = vmin - rexp
    return max(0.0, 0.8 - min(gap, 4.0) * 0.2)


def compute_pair_score(vac: pd.Series, res: pd.Series, min_token_len: int) -> PairScore:
    v_required = set(split_skills(vac.get("required_skills", "")))
    v_preferred = set(split_skills(vac.get("preferred_skills", "")))
    v_skills = v_required | v_preferred

    r_skills = set(split_skills(res.get("skills", "")))

    v_text = " ".join([
        normalize_text(vac.get("title", "")),
        normalize_text(vac.get("description", "")),
        normalize_text(vac.get("domain", "")),
        normalize_text(vac.get("seniority", "")),
    ])
    r_text = " ".join([
        normalize_text(res.get("candidate_text", "")),
        normalize_text(res.get("title", "")),
        normalize_text(res.get("last_position", "")),
        normalize_text(res.get("specialization", "")),
        normalize_text(res.get("domain", "")),
    ])

    v_tokens = tokenize(v_text, min_len=min_token_len)
    r_tokens = tokenize(r_text, min_len=min_token_len)

    required_overlap = overlap_ratio(v_required, r_skills) if v_required else 0.0
    any_skill_overlap = jaccard(v_skills, r_skills) if v_skills else 0.0
    skill_overlap = 0.7 * required_overlap + 0.3 * any_skill_overlap

    text_overlap = jaccard(v_tokens, r_tokens)

    exp_sc = experience_score(vac.get("min_experience_years", ""), res.get("total_experience_years", ""))
    meta_sc = (
        0.45 * title_position_bonus(
            normalize_text(vac.get("title", "")),
            normalize_text(res.get("title", "")),
            normalize_text(res.get("last_position", "")),
            normalize_text(res.get("specialization", "")),
        )
        + 0.35 * domain_bonus(vac.get("domain", ""), res.get("domain", ""), res.get("specialization", ""))
        + 0.20 * seniority_bonus(vac.get("seniority", ""), safe_float(res.get("total_experience_years", "")))
    )

    score = (
        0.45 * skill_overlap
        + 0.25 * text_overlap
        + 0.15 * exp_sc
        + 0.15 * meta_sc
    )

    return PairScore(
        vacancy_id=str(vac["vacancy_id"]),
        resume_id=str(res["resume_id"]),
        score=round(float(score), 6),
        skill_overlap=round(float(skill_overlap), 6),
        text_overlap=round(float(text_overlap), 6),
        exp_score=round(float(exp_sc), 6),
        meta_score=round(float(meta_sc), 6),
    )


def select_balanced_pairs(
    scored: pd.DataFrame,
    top_k: int,
    borderline_k: int,
    random_k: int,
    rng: random.Random,
) -> pd.DataFrame:
    df = scored.sort_values(["score", "resume_id"], ascending=[False, True]).reset_index(drop=True)
    n = len(df)
    if n == 0:
        return df

    selected_parts = []

    # Top matches
    top = df.head(min(top_k, n)).copy()
    top["selection_bucket"] = "top"
    selected_parts.append(top)

    already = set(top["resume_id"].tolist())

    # Borderline = around the middle score band
    remaining = df[~df["resume_id"].isin(already)].copy()
    if not remaining.empty and borderline_k > 0:
        mid = len(remaining) // 2
        half_window = max(borderline_k * 2, 6)
        start = max(0, mid - half_window)
        end = min(len(remaining), mid + half_window)
        candidate_band = remaining.iloc[start:end].copy()
        candidate_band["distance_to_median"] = (candidate_band["score"] - remaining["score"].median()).abs()
        borderline = candidate_band.sort_values(["distance_to_median", "resume_id"]).head(min(borderline_k, len(candidate_band))).copy()
        borderline["selection_bucket"] = "borderline"
        selected_parts.append(borderline)
        already.update(borderline["resume_id"].tolist())

    # Random negatives = sample from low score tail
    remaining = df[~df["resume_id"].isin(already)].copy()
    if not remaining.empty and random_k > 0:
        tail_size = max(random_k * 3, min(20, len(remaining)))
        tail = remaining.tail(min(tail_size, len(remaining))).copy()
        choices = tail["resume_id"].tolist()
        picked = set(rng.sample(choices, k=min(random_k, len(choices))))
        rnd = tail[tail["resume_id"].isin(picked)].copy()
        rnd["selection_bucket"] = "random_negative"
        selected_parts.append(rnd)

    out = pd.concat(selected_parts, ignore_index=True)
    out = out.drop_duplicates(subset=["resume_id"]).sort_values(["selection_bucket", "score"], ascending=[True, False])

    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="smart_labels_config.json", help="Path to config JSON")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    config = load_config(config_path)

    project_dir = Path(config["project_dir"])
    if not project_dir.is_absolute():
        project_dir = (config_path.parent / project_dir).resolve()

    vacancies_path = project_dir / config["vacancies_csv"]
    resumes_path = project_dir / config["resumes_csv"]
    annotations_dir = project_dir / config["annotations_dir"]
    reports_dir = project_dir / config["reports_dir"]
    annotations_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    labels_out = annotations_dir / config["output_labels_file"]
    report_out = reports_dir / config["output_report_file"]

    vacancies = pd.read_csv(vacancies_path)
    resumes = pd.read_csv(resumes_path)

    rng = random.Random(int(config["random_seed"]))
    min_token_len = int(config["min_token_len"])

    all_label_rows: List[Dict] = []
    report_rows: List[Dict] = []

    top_k = int(config["top_k"])
    borderline_k = int(config["borderline_k"])
    random_k = int(config["random_k"])
    pairs_per_vacancy = int(config["pairs_per_vacancy"])

    if top_k + borderline_k + random_k != pairs_per_vacancy:
        raise ValueError(
            f"top_k + borderline_k + random_k must equal pairs_per_vacancy. "
            f"Got {top_k}+{borderline_k}+{random_k}!={pairs_per_vacancy}"
        )

    for _, vac in vacancies.iterrows():
        pair_scores = []
        for _, res in resumes.iterrows():
            s = compute_pair_score(vac, res, min_token_len=min_token_len)
            pair_scores.append({
                "vacancy_id": s.vacancy_id,
                "resume_id": s.resume_id,
                "score": s.score,
                "skill_overlap": s.skill_overlap,
                "text_overlap": s.text_overlap,
                "exp_score": s.exp_score,
                "meta_score": s.meta_score,
                "vacancy_title": normalize_text(vac.get("title", "")),
                "resume_title": normalize_text(res.get("title", "")),
                "last_position": normalize_text(res.get("last_position", "")),
                "resume_skills": normalize_text(res.get("skills", "")),
                "vacancy_required_skills": normalize_text(vac.get("required_skills", "")),
            })

        scored_df = pd.DataFrame(pair_scores)
        selected = select_balanced_pairs(
            scored_df,
            top_k=top_k,
            borderline_k=borderline_k,
            random_k=random_k,
            rng=rng,
        )

        for _, row in selected.iterrows():
            all_label_rows.append({
                "vacancy_id": row["vacancy_id"],
                "resume_id": row["resume_id"],
                "annotator_1_label": "",
                "annotator_2_label": "",
                "final_label": "",
                "comment": "",
                "selection_bucket": row["selection_bucket"],
                "auto_score": row["score"],
            })

            report_rows.append({
                "vacancy_id": row["vacancy_id"],
                "resume_id": row["resume_id"],
                "selection_bucket": row["selection_bucket"],
                "auto_score": row["score"],
                "skill_overlap": row["skill_overlap"],
                "text_overlap": row["text_overlap"],
                "exp_score": row["exp_score"],
                "meta_score": row["meta_score"],
                "vacancy_title": row["vacancy_title"],
                "resume_title": row["resume_title"],
                "last_position": row["last_position"],
                "vacancy_required_skills": row["vacancy_required_skills"],
                "resume_skills": row["resume_skills"],
            })

    labels_df = pd.DataFrame(all_label_rows)
    report_df = pd.DataFrame(report_rows)

    labels_df.to_csv(labels_out, index=False, encoding="utf-8-sig")
    report_df.to_csv(report_out, index=False, encoding="utf-8-sig")

    print(f"Saved labels template: {labels_out}")
    print(f"Saved selection report: {report_out}")
    print(f"Vacancies: {len(vacancies)}")
    print(f"Resumes: {len(resumes)}")
    print(f"Pairs selected: {len(labels_df)}")
    print("\\nSelection buckets:")
    if not labels_df.empty:
        print(labels_df["selection_bucket"].value_counts().to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
