# Legacy

В этой папке сохранены архивные материалы проекта `ats-candidate-matching-experiment`.

Проект больше не развивается, новые эксперименты не планируются, однако при необходимости сохранённые файлы можно запускать и просматривать как архивный baseline.

# ats-candidate-matching-experiment

Набор скриптов для подготовки, разметки и оценки датасета **«вакансия — резюме»** для экспериментов по сопоставлению кандидатов и ранжированию в стиле ATS-систем.

## Что делает проект

Проект позволяет:

- парсить сырые вакансии и резюме в единый табличный формат;
- нормализовать поля: должность, навыки, опыт, домен и seniority;
- автоматически собирать пары `вакансия — резюме` для экспертной разметки;
- готовить Excel-файл для разметки без ручного merge;
- переносить старую разметку в новый раунд;
- забирать заполненную разметку из Excel обратно в CSV;
- считать метрики ранжирования после разметки.

## Структура проекта

```text
ats-candidate-matching-experiment/
├── parser_hr_dataset_v3_fixed.py
├── build_smart_labels.py
├── prepare_annotation_round.py
├── finalize_annotation_round.py
├── evaluate_matching_metrics.py
├── skills_map_example.json
├── parser_config.json
├── smart_labels_config.json
├── metrics_config.json
├── raw/
│   ├── vacancies/
│   └── resumes/
├── processed/
├── annotations/
└── reports/
```

## Основные скрипты

### `parser_hr_dataset_v3_fixed.py`
Парсит исходные вакансии и резюме и сохраняет:

- `processed/vacancies.csv`
- `processed/resumes.csv`
- `annotations/labels_template.csv`
- `reports/parse_log.csv`

### `build_smart_labels.py`
Формирует более полезный шаблон разметки на основе грубого автоматического score:

- `annotations/labels_template_smart.csv`
- `reports/pair_selection_report.csv`

На каждую вакансию выбираются:
- сильные совпадения;
- пограничные случаи;
- вероятно нерелевантные пары.

### `prepare_annotation_round.py`
Подготавливает новый раунд разметки **без ручных действий**:

- берёт `annotations/labels_template_smart.csv`;
- если есть старый `annotations/labels_template_smart_filled.csv`, переносит старые метки;
- создаёт:
  - `annotations/labels_template_smart_merged.csv`
  - `annotations/labels_template_smart_merged.xlsx`

Excel-файл сразу готов для разметки:
- есть выпадающие значения `0 / 1 / 2`;
- закреплена шапка;
- включён автофильтр.

### `finalize_annotation_round.py`
Завершает раунд разметки **без ручного экспорта в CSV**:

- читает `annotations/labels_template_smart_merged.xlsx`;
- сохраняет:
  - `annotations/labels_template_smart_filled.csv`;
- считает метрики и пишет отчёты в `reports/`.

### `evaluate_matching_metrics.py`
Отдельный скрипт для подсчёта метрик по размеченному CSV, если нужен ручной запуск.

## Быстрый старт

### 1. Создать и активировать виртуальное окружение

Windows:

```bash
py -m venv .venv
.\.venv\Scripts\activate
```

### 2. Установить зависимости

```bash
py -m pip install pandas python-docx openpyxl
```

### 3. Распарсить исходные данные

```bash
py parser_hr_dataset_v3_fixed.py
```

После запуска будут созданы:
- `processed/vacancies.csv`
- `processed/resumes.csv`
- `reports/parse_log.csv`

### 4. Сгенерировать пары для разметки

```bash
py build_smart_labels.py
```

После запуска будут созданы:
- `annotations/labels_template_smart.csv`
- `reports/pair_selection_report.csv`

### 5. Подготовить Excel для нового раунда разметки

```bash
py prepare_annotation_round.py
```

После запуска будут созданы:
- `annotations/labels_template_smart_merged.csv`
- `annotations/labels_template_smart_merged.xlsx`

### 6. Выполнить разметку в Excel

Открыть файл:

```text
annotations/labels_template_smart_merged.xlsx
```

Заполняются только поля:
- `annotator_1_label`
- `annotator_2_label`
- `final_label`
- `comment`

### 7. Завершить раунд и посчитать метрики

```bash
py finalize_annotation_round.py
```

После запуска будут созданы:
- `annotations/labels_template_smart_filled.csv`
- `reports/metrics_by_vacancy.csv`
- `reports/ranked_pairs_scored.csv`
- `reports/label_distribution.csv`
- `reports/metrics_summary.json`
- `reports/metrics_report.txt`

## Шкала разметки

Для экспертной оценки используется шкала:

- `2` — релевантен;
- `1` — частично релевантен;
- `0` — нерелевантен.

## Основной рабочий цикл

### Первый запуск
1. `py parser_hr_dataset_v3_fixed.py`
2. `py build_smart_labels.py`
3. `py prepare_annotation_round.py`
4. Разметка в `labels_template_smart_merged.xlsx`
5. `py finalize_annotation_round.py`

### Следующий раунд
1. снова `py build_smart_labels.py`
2. снова `py prepare_annotation_round.py`
3. старые метки будут перенесены автоматически;
4. разметить только новые строки;
5. снова `py finalize_annotation_round.py`

## Назначение основных файлов

### `processed/vacancies.csv`
Нормализованные вакансии.

### `processed/resumes.csv`
Нормализованные резюме.

### `annotations/labels_template_smart.csv`
Новый шаблон разметки после генерации пар.

### `annotations/labels_template_smart_merged.xlsx`
Основной рабочий Excel-файл для разметки.

### `annotations/labels_template_smart_filled.csv`
Итоговая разметка после завершения раунда.

### `reports/parse_log.csv`
Лог парсинга.

### `reports/pair_selection_report.csv`
Отчёт по автоматическому отбору пар.

### `reports/metrics_by_vacancy.csv`
Метрики по каждой вакансии.

### `reports/metrics_report.txt`
Краткий текстовый отчёт по средним метрикам.
