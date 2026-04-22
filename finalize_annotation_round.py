#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Zero-config finalize script.

What it does:
1. Reads:
   annotations/labels_template_smart_merged.xlsx
2. Writes:
   annotations/labels_template_smart_filled.csv
3. Computes metrics and saves reports into reports/

Run:
    py finalize_annotation_round.py
"""

from __future__ import annotations

from pathlib import Path
import json
import math
from typing import Dict, List, Optional

import pandas as pd

PROJECT_DIR = Path(".").resolve()
ANNOTATIONS_DIR = PROJECT_DIR / "annotations"
REPORTS_DIR = PROJECT_DIR / "reports"

INPUT_XLSX = ANNOTATIONS_DIR / "labels_template_smart_merged.xlsx"
OUTPUT_FILLED_CSV = ANNOTATIONS_DIR / "labels_template_smart_filled.csv"

KS = [5, 10]
STRICT_LABELS = {2}
RELAXED_LABELS = {1, 2}


def to_float(value) -> Optional[float]:
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def to_int_label(value) -> Optional[int]:
    if pd.isna(value):
        return None
    text = str(value).strip()
    if text == "":
        return None
    text = text.replace(",", ".")
    try:
        return int(float(text))
    except ValueError:
        return None


def resolve_final_label(row: pd.Series) -> Optional[int]:
    final_label = to_int_label(row.get("final_label"))
    if final_label is not None:
        return final_label

    a1 = to_int_label(row.get("annotator_1_label"))
    a2 = to_int_label(row.get("annotator_2_label"))
    if a1 is not None and a2 is not None and a1 == a2:
        return a1

    return None


def dcg(labels: List[int], k: int) -> float:
    total = 0.0
    for i, rel in enumerate(labels[:k], start=1):
        total += (2 ** rel - 1) / math.log2(i + 1)
    return total


def ndcg(labels: List[int], k: int) -> float:
    actual = dcg(labels, k)
    ideal = dcg(sorted(labels, reverse=True), k)
    return 0.0 if ideal == 0 else actual / ideal


def precision_at_k(binary_labels: List[int], k: int) -> float:
    top = binary_labels[:k]
    return 0.0 if not top else sum(top) / len(top)


def recall_at_k(binary_labels: List[int], k: int) -> float:
    total_relevant = sum(binary_labels)
    return 0.0 if total_relevant == 0 else sum(binary_labels[:k]) / total_relevant


def mrr(binary_labels: List[int]) -> float:
    for i, rel in enumerate(binary_labels, start=1):
        if rel:
            return 1.0 / i
    return 0.0


def compute_vacancy_metrics(group: pd.DataFrame) -> Dict:
    labels = group["resolved_label"].tolist()
    strict_binary = [1 if x in STRICT_LABELS else 0 for x in labels]
    relaxed_binary = [1 if x in RELAXED_LABELS else 0 for x in labels]

    row = {
        "vacancy_id": group["vacancy_id"].iloc[0],
        "n_pairs": int(len(group)),
        "n_strict_relevant": int(sum(strict_binary)),
        "n_relaxed_relevant": int(sum(relaxed_binary)),
        "mrr_strict": round(mrr(strict_binary), 6),
        "mrr_relaxed": round(mrr(relaxed_binary), 6),
    }

    for k in KS:
        row[f"precision_strict@{k}"] = round(precision_at_k(strict_binary, k), 6)
        row[f"precision_relaxed@{k}"] = round(precision_at_k(relaxed_binary, k), 6)
        row[f"recall_strict@{k}"] = round(recall_at_k(strict_binary, k), 6)
        row[f"recall_relaxed@{k}"] = round(recall_at_k(relaxed_binary, k), 6)
        row[f"ndcg@{k}"] = round(ndcg(labels, k), 6)

    return row


def main() -> int:
    ANNOTATIONS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    if not INPUT_XLSX.exists():
        raise FileNotFoundError(f"Не найден Excel с разметкой: {INPUT_XLSX}")

    df = pd.read_excel(INPUT_XLSX, sheet_name="Разметка")
    df.to_csv(OUTPUT_FILLED_CSV, index=False, encoding="utf-8-sig")

    df["resolved_label"] = df.apply(resolve_final_label, axis=1)
    df["_score_num"] = df["auto_score"].apply(to_float)

    labeled = df[df["resolved_label"].notna()].copy()
    if labeled.empty:
        raise ValueError("Нет размеченных пар. Заполни final_label или согласованные annotator_1_label и annotator_2_label.")

    labeled["resolved_label"] = labeled["resolved_label"].astype(int)
    labeled = labeled.sort_values(["vacancy_id", "_score_num", "resume_id"], ascending=[True, False, True]).copy()
    labeled["rank"] = labeled.groupby("vacancy_id").cumcount() + 1

    per_vacancy = []
    for _, group in labeled.groupby("vacancy_id", sort=True):
        per_vacancy.append(compute_vacancy_metrics(group))

    metrics_by_vacancy = pd.DataFrame(per_vacancy)
    metric_cols = [c for c in metrics_by_vacancy.columns if c not in {"vacancy_id", "n_pairs", "n_strict_relevant", "n_relaxed_relevant"}]

    summary = {
        "n_total_pairs": int(len(df)),
        "n_labeled_pairs": int(len(labeled)),
        "n_unlabeled_pairs": int(len(df) - len(labeled)),
        "n_vacancies": int(labeled["vacancy_id"].nunique()),
        "overall_mean": {col: round(float(metrics_by_vacancy[col].mean()), 6) for col in metric_cols},
    }

    label_distribution = (
        labeled.groupby(["selection_bucket", "resolved_label"])
        .size()
        .reset_index(name="count")
        .sort_values(["selection_bucket", "resolved_label"])
    )

    ranked_pairs = labeled[[
        "vacancy_id", "resume_id", "selection_bucket", "auto_score", "_score_num", "resolved_label", "rank"
    ]].rename(columns={"_score_num": "score_num"})

    metrics_by_vacancy.to_csv(REPORTS_DIR / "metrics_by_vacancy.csv", index=False, encoding="utf-8-sig")
    ranked_pairs.to_csv(REPORTS_DIR / "ranked_pairs_scored.csv", index=False, encoding="utf-8-sig")
    label_distribution.to_csv(REPORTS_DIR / "label_distribution.csv", index=False, encoding="utf-8-sig")
    (REPORTS_DIR / "metrics_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "Отчёт по метрикам сопоставления вакансий и резюме",
        "",
        f"Всего пар: {summary['n_total_pairs']}",
        f"Размеченных пар: {summary['n_labeled_pairs']}",
        f"Неразмеченных пар: {summary['n_unlabeled_pairs']}",
        f"Количество вакансий: {summary['n_vacancies']}",
        "",
        "Средние метрики по вакансиям:"
    ]
    for key, value in summary["overall_mean"].items():
        lines.append(f"- {key}: {value}")
    (REPORTS_DIR / "metrics_report.txt").write_text("\n".join(lines), encoding="utf-8")

    print(f"Готово: {OUTPUT_FILLED_CSV}")
    print(f"Готово: {REPORTS_DIR / 'metrics_by_vacancy.csv'}")
    print(f"Готово: {REPORTS_DIR / 'ranked_pairs_scored.csv'}")
    print(f"Готово: {REPORTS_DIR / 'label_distribution.csv'}")
    print(f"Готово: {REPORTS_DIR / 'metrics_summary.json'}")
    print(f"Готово: {REPORTS_DIR / 'metrics_report.txt'}")
    print("")
    print("Средние метрики:")
    for key, value in summary["overall_mean"].items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
