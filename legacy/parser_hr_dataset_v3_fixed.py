#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Zero-config HR dataset parser for IDE usage.

How it works:
- Put this script into your project root or scripts/ directory.
- Put parser_config.json next to the script.
- Click Run in IDE. No CLI arguments required.
- The script reads raw vacancies/resumes, parses them, and writes:
    processed/vacancies.csv
    processed/resumes.csv
    annotations/labels_template.csv   (optional)
    reports/parse_log.csv

Optional CLI usage still works:
    python parser_hr_dataset_v3.py --config ./parser_config.json
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any

CURRENT_YEAR = datetime.now().year


DEFAULT_CONFIG = {
    "project_dir": ".",
    "raw_vacancies_dir": "raw/vacancies",
    "raw_resumes_dir": "raw/resumes",
    "processed_dir": "processed",
    "annotations_dir": "annotations",
    "reports_dir": "reports",
    "generate_labels_template": True,
    "pairs_per_vacancy": 20,
    "shuffle_pairs": False,
    "skills_map_path": "skills_map_example.json",
    "file_patterns": ["*.txt", "*.md", "*.csv", "*.tsv", "*.json", "*.jsonl", "*.docx"]
}


@dataclass
class ParseLogRow:
    kind: str
    source_file: str
    status: str
    records_count: int
    details: str


def safe_read_text(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except Exception:
            continue
    return path.read_text(errors="ignore")


def clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_skill_name(name: Any) -> str:
    if name is None:
        return ""
    if isinstance(name, (list, tuple, set)):
        name = " ".join(str(x) for x in name if x is not None)
    return re.sub(r"\s+", " ", str(name).strip())


def load_skills_map(config_path: Path, skills_map_value: str) -> Dict[str, str]:
    if not skills_map_value:
        return {}
    skills_path = Path(skills_map_value)
    if not skills_path.is_absolute():
        skills_path = (config_path.parent / skills_path).resolve()
    if not skills_path.exists():
        return {}

    data = json.loads(skills_path.read_text(encoding="utf-8"))
    out: Dict[str, str] = {}

    # Supported formats:
    # 1) {"java": "Java", "springboot": "Spring Boot"}
    # 2) {"Spring Boot": ["spring boot", "springboot"], "SQL": ["sql", "postgresql", "mysql"]}
    # 3) {"Spring Boot": {"canonical": "Spring Boot", "aliases": ["spring boot", "springboot"]}}
    if isinstance(data, dict):
        for raw, norm in data.items():
            if isinstance(norm, str):
                alias = normalize_skill_name(raw)
                canonical = normalize_skill_name(norm) or alias
                if alias:
                    out[alias.lower()] = canonical
            elif isinstance(norm, (list, tuple, set)):
                canonical = normalize_skill_name(raw)
                if canonical:
                    out[canonical.lower()] = canonical
                for alias in norm:
                    alias_name = normalize_skill_name(alias)
                    if alias_name and canonical:
                        out[alias_name.lower()] = canonical
            elif isinstance(norm, dict):
                canonical = normalize_skill_name(norm.get("canonical", raw))
                aliases = norm.get("aliases", [])
                if canonical:
                    out[canonical.lower()] = canonical
                for alias in aliases:
                    alias_name = normalize_skill_name(alias)
                    if alias_name and canonical:
                        out[alias_name.lower()] = canonical

    return out


def extract_years_experience(text: str) -> Optional[int]:
    lower = text.lower()

    patterns = [
        r"(\d+)\+\s*(?:years|yrs)\s*(?:of\s+)?(?:total\s+)?experience",
        r"experience\s*[:\-]?\s*(\d+)\+?\s*(?:years|yrs)",
        r"(\d+)\s*-\s*(\d+)\s*years\s*of\s*experience",
    ]
    for pattern in patterns:
        m = re.search(pattern, lower, re.IGNORECASE)
        if m:
            nums = [int(x) for x in m.groups() if x]
            return max(nums) if nums else None

    total = 0.0
    found_any = False
    # Handles 2020-now / 2017-2019 / 2020-present
    for start_s, end_s in re.findall(r"\b(19\d{2}|20\d{2})\s*[-–]\s*(now|present|current|19\d{2}|20\d{2})\b", lower):
        try:
            start = int(start_s)
            end = CURRENT_YEAR if end_s in {"now", "present", "current"} else int(end_s)
            if end >= start:
                total += (end - start) or 1
                found_any = True
        except Exception:
            pass

    if found_any:
        return max(1, int(round(total)))
    return None


def infer_seniority(text: str) -> str:
    lower = text.lower()
    if "junior" in lower or "entry level" in lower:
        return "junior"
    if "senior" in lower or "lead" in lower or "principal" in lower:
        return "senior"
    if "middle" in lower or "mid-level" in lower or "mid level" in lower:
        return "middle"
    return ""


def infer_domain(text: str) -> str:
    lower = text.lower()

    if any(x in lower for x in ("machine learning", "ml engineer", "data scientist", "deep learning")):
        return "machine learning"

    if any(x in lower for x in ("data analyst", "analytics", "bi ", "business intelligence")):
        return "analytics"

    # Сначала .NET, потом generic backend
    if any(x in lower for x in (".net", "dotnet", "c#", "c sharp", "asp.net", "asp net")):
        return ".net software development"

    if any(x in lower for x in ("backend", "spring boot", "java developer", "rest api")):
        return "backend software development"

    if any(x in lower for x in ("frontend", "react", "javascript", "html", "css")):
        return "frontend software development"

    return "software engineering"



def infer_education(text: str) -> str:
    lower = text.lower()
    if "phd" in lower or "doctor" in lower:
        return "phd"
    if "master" in lower:
        return "master"
    if "bachelor" in lower:
        return "bachelor"
    return ""


def extract_languages(text: str) -> List[str]:
    m = re.search(r"languages?\s*[:\-]\s*([^\n]+)", text, re.IGNORECASE)
    if not m:
        return []
    chunk = m.group(1)
    parts = [x.strip(" .") for x in re.split(r"[,;/]", chunk) if x.strip()]
    return parts

#фикс 
def extract_skills(text: str, skills_map: Dict[str, str]) -> List[str]:
    lower = text.lower()
    found = set()

    for raw, norm in skills_map.items():
        raw_norm = raw.lower().strip()
        if not raw_norm:
            continue

        raw_escaped = re.escape(raw_norm)

        if re.search(r"[a-zA-Z0-9]", raw_norm):
            pattern = rf"(?<![a-zA-Z0-9]){raw_escaped}(?![a-zA-Z0-9])"
        else:
            pattern = raw_escaped

        if re.search(pattern, lower, re.IGNORECASE):
            found.add(norm)

    return sorted(found)


def guess_title_from_lines(lines: List[str]) -> str:
    if not lines:
        return ""
    for line in lines[:6]:
        s = line.strip()
        if 3 <= len(s) <= 120 and not re.search(r"phone|email|linkedin|location", s, re.IGNORECASE):
            return s
    return lines[0].strip()


def read_docx(path: Path) -> str:
    try:
        from docx import Document  # type: ignore
    except Exception as exc:
        raise RuntimeError("python-docx is not installed. Install it with: pip install python-docx") from exc

    doc = Document(str(path))
    parts = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
    return "\n".join(parts)


def rows_from_csv(path: Path) -> List[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def rows_from_tsv(path: Path) -> List[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        return [dict(row) for row in reader]


def rows_from_json(path: Path) -> List[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [dict(x) for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def rows_from_jsonl(path: Path) -> List[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        if isinstance(item, dict):
            rows.append(item)
    return rows


def iter_files(base_dir: Path, patterns: List[str]) -> List[Path]:
    items: List[Path] = []
    if not base_dir.exists():
        return items
    for pattern in patterns:
        items.extend(base_dir.rglob(pattern))
    unique = sorted(set(items))
    return [p for p in unique if p.is_file()]

'''
фикс parse_vacancy_text
Responsibilities: и Requirements: не будут попадать в title
если в начале файла есть нормальный заголовок типа Backend Software Developer, он возьмётся
если заголовка нет, возьмётся первая осмысленная строка

Если вакансии у тебя уже приходят из csv с отдельным полем job_title, этот патч почти не нужен. Он полезен именно для .txt/.md/.docx, где title надо вытаскивать из текста.
'''
VACANCY_NOISE_PREFIXES = (
    "job description", "responsibilities", "requirements", "required skills",
    "preferred skills", "about us", "about the company", "location",
    "employment type", "salary", "we offer", "what we offer"
)

def is_vacancy_noise_line(line: str) -> bool:
    s = (line or "").strip().lower()
    if not s:
        return True
    return any(s.startswith(prefix) for prefix in VACANCY_NOISE_PREFIXES)

def looks_like_vacancy_title(line: str) -> bool:
    s = (line or "").strip()
    low = s.lower()
    if not s or len(s) > 120:
        return False
    if is_vacancy_noise_line(s):
        return False
    return any(x in low for x in (
        "developer", "engineer", "architect", "analyst", "manager",
        "designer", "scientist", "qa", "devops", "administrator",
        ".net", "backend", "frontend", "full stack", "fullstack"
    ))

def extract_vacancy_title(lines: list[str]) -> str:
    for line in lines[:20]:
        line = line.strip()
        if not line or is_vacancy_noise_line(line):
            continue
        if looks_like_vacancy_title(line):
            return line
    for line in lines[:20]:
        line = line.strip()
        if line and not is_vacancy_noise_line(line):
            return line
    return ""

def parse_vacancy_text(path: Path, text: str, skills_map: Dict[str, str], fallback_id: int) -> List[dict]:
    description = clean_text(text)
    lines = [x.strip() for x in description.splitlines() if x.strip()]
    title = extract_vacancy_title(lines)

    return [{
        "vacancy_id": f"V{fallback_id:05d}",
        "title": title,
        "description": description,
        "required_skills": ";".join(extract_skills(description, skills_map)),
        "preferred_skills": "",
        "min_experience_years": extract_years_experience(description) or "",
        "education_level": infer_education(description),
        "domain": infer_domain(description),
        "seniority": infer_seniority(description),
        "location": "",
        "employment_type": "",
        "source_file": path.name,
    }]
'''
фикс parse_resume_text
title будет браться как первая строка, похожая на должность
Address, Phone, Email больше не будут попадать в title
last_position будет отдельным полем, а не копией title
'''


SERVICE_PREFIXES = (
    "phone", "address", "portfolio", "email", "linkedin", "location"
)

JOB_TITLE_HINTS = (
    "developer", "engineer", "architect", "programmer", "analyst",
    "lead", "senior", "middle", "junior", "software", ".net", "backend",
    "frontend", "full stack", "fullstack", "c#", "qa", "devops"
)

def is_service_line(line: str) -> bool:
    s = (line or "").strip().lower()
    if not s:
        return True
    return any(s.startswith(prefix + ":") or s == prefix for prefix in SERVICE_PREFIXES)

def looks_like_job_title(line: str) -> bool:
    s = (line or "").strip().lower()
    if not s or len(s) > 120:
        return False
    if is_service_line(s):
        return False
    return any(hint in s for hint in JOB_TITLE_HINTS)

def extract_resume_title(lines: list[str]) -> str:
    for line in lines[:20]:
        line = line.strip()
        if not line or is_service_line(line):
            continue
        if looks_like_job_title(line):
            return line
    for line in lines[:20]:
        line = line.strip()
        if line and not is_service_line(line):
            return line
    return ""

def extract_last_position(text: str, fallback_title: str = "") -> str:
    lines = [x.strip() for x in text.splitlines() if x.strip()]

    # 1. Сначала ищем начало секции опыта
    experience_headers = (
        "experience",
        "professional experience",
        "work experience",
        "employment history"
    )

    exp_start = None
    for i, line in enumerate(lines):
        low = line.lower()
        if any(h in low for h in experience_headers):
            exp_start = i
            break

    # 2. Если нашли секцию опыта, ищем первую должность после неё
    if exp_start is not None:
        exp_lines = lines[exp_start + 1 : min(len(lines), exp_start + 40)]

        for i, line in enumerate(exp_lines):
            low = line.lower()

            # пропускаем мусорные и явно не должностные строки
            if is_service_line(line):
                continue
            if low.startswith(("achievement", "responsibility", "responsibilities", "education", "certifications", "skills")):
                continue

            # если строка похожа на дату/период, пропускаем
            if re.search(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b", low):
                continue
            if re.search(r"\b(19|20)\d{2}\b", low) and len(line) < 40:
                continue

            if looks_like_job_title(line):
                return line

            # Частый паттерн: Company -> Position -> Date
            if i + 1 < len(exp_lines):
                nxt = exp_lines[i + 1].strip()
                if looks_like_job_title(nxt):
                    return nxt

    # 3. Если секция опыта не помогла, ищем по всему тексту, но осторожно
    for i, line in enumerate(lines[:120]):
        low = line.lower()

        if is_service_line(line):
            continue
        if low.startswith(("skills", "education", "certifications", "languages")):
            continue

        # не брать строки из блока навыков/технологий
        if sum(ch in line for ch in ",;") >= 2:
            continue

        if looks_like_job_title(line):
            prev_line = lines[i - 1].lower() if i > 0 else ""
            if prev_line.startswith(("phone", "address", "portfolio", "email", "skills", "education", "certifications")):
                continue
            return line

    return fallback_title


def parse_resume_text(path: Path, text: str, skills_map: Dict[str, str], fallback_id: int) -> List[dict]:
    cleaned = clean_text(text)
    lines = [x.strip() for x in cleaned.splitlines() if x.strip()]

    title = extract_resume_title(lines)
    last_position = extract_last_position(cleaned, fallback_title=title)

    return [{
        "resume_id": f"R{fallback_id:05d}",
        "candidate_text": cleaned,
        "skills": ";".join(extract_skills(cleaned, skills_map)),
        "total_experience_years": extract_years_experience(cleaned) or "",
        "last_position": last_position,
        "education": infer_education(cleaned),
        "specialization": "",
        "domain": infer_domain(cleaned),
        "language_level": "",
        "languages": ";".join(extract_languages(cleaned)),
        "certifications": "",
        "location": "",
        "source_file": path.name,
        "title": title,
    }]


def normalize_vacancy_row(row: dict, source_file: str, fallback_id: int, skills_map: Dict[str, str]) -> dict:
    raw_title = (row.get("title") or row.get("job_title") or row.get("name") or "").strip()
    raw_description = (
        row.get("description")
        or row.get("job_description")
        or row.get("text")
        or row.get("content")
        or ""
    ).strip()

    combined = clean_text(f"{raw_title}\n{raw_description}".strip())
    lines = [x.strip() for x in combined.splitlines() if x.strip()]

    title = raw_title if raw_title else extract_vacancy_title(lines)

    return {
        "vacancy_id": str(row.get("vacancy_id") or f"V{fallback_id:05d}"),
        "title": title,
        "description": combined,
        "required_skills": ";".join(extract_skills(combined, skills_map)),
        "preferred_skills": normalize_skill_name(row.get("preferred_skills") or "").replace(",", ";"),
        "min_experience_years": row.get("min_experience_years") or extract_years_experience(combined) or "",
        "education_level": row.get("education_level") or infer_education(combined),
        "domain": row.get("domain") or infer_domain(combined),
        "seniority": row.get("seniority") or infer_seniority(combined),
        "location": row.get("location") or "",
        "employment_type": row.get("employment_type") or "",
        "source_file": source_file,
    }


def normalize_resume_row(row: dict, source_file: str, fallback_id: int, skills_map: Dict[str, str]) -> dict:
    raw_text = (
        row.get("candidate_text")
        or row.get("resume_text")
        or row.get("text")
        or row.get("content")
        or ""
    )

    raw_title = (row.get("title") or "").strip()
    raw_last_position = (row.get("last_position") or "").strip()

    combined = clean_text(f"{raw_title}\n{raw_text}".strip())
    lines = [x.strip() for x in combined.splitlines() if x.strip()]

    title = raw_title if raw_title else extract_resume_title(lines)
    last_position = raw_last_position if raw_last_position else extract_last_position(combined, fallback_title=title)

    return {
        "resume_id": str(row.get("resume_id") or f"R{fallback_id:05d}"),
        "candidate_text": combined,
        "skills": (
            ";".join(extract_skills(combined, skills_map))
            if not row.get("skills")
            else str(row.get("skills")).replace(",", ";")
        ),
        "total_experience_years": row.get("total_experience_years") or extract_years_experience(combined) or "",
        "last_position": last_position,
        "education": row.get("education") or infer_education(combined),
        "specialization": row.get("specialization") or "",
        "domain": row.get("domain") or infer_domain(combined),
        "language_level": row.get("language_level") or "",
        "languages": row.get("languages") or ";".join(extract_languages(combined)),
        "certifications": row.get("certifications") or "",
        "location": row.get("location") or "",
        "source_file": source_file,
        "title": title,
    }

def parse_files(base_dir: Path, kind: str, patterns: List[str], skills_map: Dict[str, str]) -> Tuple[List[dict], List[ParseLogRow]]:
    records: List[dict] = []
    logs: List[ParseLogRow] = []
    files = iter_files(base_dir, patterns)
    counter = 1

    for path in files:
        try:
            suffix = path.suffix.lower()
            if suffix in {".txt", ".md"}:
                text = safe_read_text(path)
                rows = parse_vacancy_text(path, text, skills_map, counter) if kind == "vacancy" else parse_resume_text(path, text, skills_map, counter)

            elif suffix == ".docx":
                text = read_docx(path)
                rows = parse_resume_text(path, text, skills_map, counter) if kind == "resume" else parse_vacancy_text(path, text, skills_map, counter)

            elif suffix == ".csv":
                raw_rows = rows_from_csv(path)
                rows = [
                    normalize_vacancy_row(r, path.name, counter + i, skills_map) if kind == "vacancy"
                    else normalize_resume_row(r, path.name, counter + i, skills_map)
                    for i, r in enumerate(raw_rows)
                ]

            elif suffix == ".tsv":
                raw_rows = rows_from_tsv(path)
                rows = [
                    normalize_vacancy_row(r, path.name, counter + i, skills_map) if kind == "vacancy"
                    else normalize_resume_row(r, path.name, counter + i, skills_map)
                    for i, r in enumerate(raw_rows)
                ]

            elif suffix == ".json":
                raw_rows = rows_from_json(path)
                rows = [
                    normalize_vacancy_row(r, path.name, counter + i, skills_map) if kind == "vacancy"
                    else normalize_resume_row(r, path.name, counter + i, skills_map)
                    for i, r in enumerate(raw_rows)
                ]

            elif suffix == ".jsonl":
                raw_rows = rows_from_jsonl(path)
                rows = [
                    normalize_vacancy_row(r, path.name, counter + i, skills_map) if kind == "vacancy"
                    else normalize_resume_row(r, path.name, counter + i, skills_map)
                    for i, r in enumerate(raw_rows)
                ]
            else:
                logs.append(ParseLogRow(kind, path.name, "skipped", 0, f"unsupported extension: {suffix}"))
                continue

            records.extend(rows)
            counter += len(rows)
            logs.append(ParseLogRow(kind, path.name, "ok", len(rows), ""))
        except Exception as exc:
            logs.append(ParseLogRow(kind, path.name, "error", 0, str(exc)))

    return records, logs


def write_csv(path: Path, rows: List[dict], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            safe_row = {k: row.get(k, "") for k in fieldnames}
            writer.writerow(safe_row)


def build_labels_template(vacancies: List[dict], resumes: List[dict], pairs_per_vacancy: int) -> List[dict]:
    labels = []
    limited_resumes = resumes[:max(0, pairs_per_vacancy)]
    for v in vacancies:
        for r in limited_resumes:
            labels.append({
                "vacancy_id": v["vacancy_id"],
                "resume_id": r["resume_id"],
                "annotator_1_label": "",
                "annotator_2_label": "",
                "final_label": "",
                "comment": "",
            })
    return labels


def load_config(config_path: Path) -> dict:
    if config_path.exists():
        user_cfg = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        user_cfg = {}
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(user_cfg)
    return cfg


def resolve_path(config_path: Path, raw_path: str) -> Path:
    p = Path(raw_path)
    if p.is_absolute():
        return p
    return (config_path.parent / p).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="HR dataset parser with local JSON config.")
    parser.add_argument("--config", default=None, help="Path to config file. Defaults to parser_config.json next to this script.")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    config_path = Path(args.config).resolve() if args.config else (script_dir / "parser_config.json")
    config = load_config(config_path)

    project_dir = resolve_path(config_path, config["project_dir"])
    raw_vacancies_dir = project_dir / config["raw_vacancies_dir"]
    raw_resumes_dir = project_dir / config["raw_resumes_dir"]
    processed_dir = project_dir / config["processed_dir"]
    annotations_dir = project_dir / config["annotations_dir"]
    reports_dir = project_dir / config["reports_dir"]
    patterns = list(config.get("file_patterns", DEFAULT_CONFIG["file_patterns"]))

    project_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    annotations_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    skills_map = load_skills_map(config_path, config.get("skills_map_path", ""))

    vacancies, v_logs = parse_files(raw_vacancies_dir, "vacancy", patterns, skills_map)
    resumes, r_logs = parse_files(raw_resumes_dir, "resume", patterns, skills_map)

    vacancy_fields = [
        "vacancy_id", "title", "description", "required_skills", "preferred_skills",
        "min_experience_years", "education_level", "domain", "seniority",
        "location", "employment_type", "source_file"
    ]
    resume_fields = [
        "resume_id", "candidate_text", "skills", "total_experience_years", "last_position",
        "education", "specialization", "domain", "language_level", "languages",
        "certifications", "location", "source_file", "title"
    ]
    label_fields = [
        "vacancy_id", "resume_id", "annotator_1_label", "annotator_2_label",
        "final_label", "comment"
    ]
    log_fields = ["kind", "source_file", "status", "records_count", "details"]

    write_csv(processed_dir / "vacancies.csv", vacancies, vacancy_fields)
    write_csv(processed_dir / "resumes.csv", resumes, resume_fields)

    if config.get("generate_labels_template", True):
        labels = build_labels_template(
            vacancies, resumes, int(config.get("pairs_per_vacancy", 20))
        )
        write_csv(annotations_dir / "labels_template.csv", labels, label_fields)

    write_csv(reports_dir / "parse_log.csv", [asdict(x) for x in (v_logs + r_logs)], log_fields)

    print("Done.")
    print(f"Config: {config_path}")
    print(f"Project dir: {project_dir}")
    print(f"Vacancies parsed: {len(vacancies)}")
    print(f"Resumes parsed: {len(resumes)}")
    print(f"Output: {processed_dir / 'vacancies.csv'}")
    print(f"Output: {processed_dir / 'resumes.csv'}")
    if config.get("generate_labels_template", True):
        print(f"Output: {annotations_dir / 'labels_template.csv'}")
    print(f"Log: {reports_dir / 'parse_log.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
