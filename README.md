# ats-candidate-matching-experiment

Проект для экспериментальной проверки гипотезы о наличии «слепого пятна» в алгоритмическом первичном отборе кандидатов.

## Замечание

Текущий эксперимент сознательно уходит от опоры на старый rule-based парсер.  
Это сделано для того, чтобы проверять не качество автоматического извлечения навыков, а саму гипотезу о зависимости результата отбора от формы представления одного и того же опыта.

## Основная идея

Проект не пытается ответить на вопрос, «насколько хорошо ATS подбирает людей вообще».  
Его задача — проверить, может ли алгоритмический отбор **по-разному оценивать одного и того же содержательно релевантного кандидата** в зависимости от формы представления опыта в резюме.

В эксперименте используются несколько версий резюме одного кандидата:

- `original` — обычная версия;
- `ats_optimized` — версия с более явной структурой и ключевыми формулировками;
- `ai_assisted` — версия, переписанная с помощью ИИ;
- `weakly_structured` — содержательно близкая, но менее удобная для машинной интерпретации версия.

Если при неизменном содержании опыта результат отбора меняется, это рассматривается как возможное проявление «слепого пятна».

## Актуальная структура проекта

```text
ats-candidate-matching-experiment/
├── build_smart_labels_experiment.py
├── prepare_annotation_round.py
├── finalize_annotation_round.py
├── evaluate_matching_metrics.py
├── smart_labels_config_experiment.json
├── metrics_config.json
├── README.md
├── legacy/
│   ├── parser_hr_dataset_v3_fixed.py
│   ├── parser_config.json
│   ├── skills_map_example.json
│   ├── build_smart_labels.py
│   └── LEGACY.md
├── raw/
│   ├── vacancies/
│   └── resumes/
├── processed/
│   ├── vacancies.csv
│   └── resumes_experiment.csv
├── annotations/
├── reports/
└── templates/
    └── annotation_template_blank.xlsx
```

## Что находится в корне проекта

### `build_smart_labels_experiment.py`
Основной генератор пар для экспериментального сценария.

Что делает:
- читает `processed/vacancies.csv`;
- читает `processed/resumes_experiment.csv`;
- считает `auto_score` для каждой пары;
- сохраняет `candidate_id` и `version_type`;
- дедуплицирует выборку по `candidate_id`, чтобы несколько версий одного кандидата не занимали сразу несколько мест;
- формирует шаблон для ручной разметки.

Выход:
- `annotations/labels_template_smart_experiment.csv`
- `reports/pair_selection_report_experiment.csv`

### `prepare_annotation_round.py`
Подготавливает Excel-файл для разметки:
- читает свежий CSV-шаблон;
- переносит старые метки, если они уже есть;
- создаёт:
  - `annotations/labels_template_smart_merged.csv`
  - `annotations/labels_template_smart_merged.xlsx`

### `finalize_annotation_round.py`
Завершает раунд разметки:
- читает заполненный Excel;
- сохраняет заполненный CSV;
- считает метрики;
- пишет отчёты в `reports/`.

### `evaluate_matching_metrics.py`
Отдельный скрипт для расчёта метрик по уже размеченному CSV, если нужен ручной запуск вне основного workflow.

### `smart_labels_config_experiment.json`
Конфиг экспериментального генератора пар.

### `metrics_config.json`
Конфиг для расчёта метрик.

## Папка `legacy/`

В `legacy/` вынесены ранние и вспомогательные компоненты:

- `parser_hr_dataset_v3_fixed.py`
- `parser_config.json`
- `skills_map_example.json`
- `build_smart_labels.py`

Эти файлы сохранены как legacy/baseline-компоненты и **не используются как основа текущего экспериментального сценария**.

Причина:
rule-based парсинг и словарное извлечение навыков создают слишком сильный шум и начинают измерять качество парсера, а не наличие «слепого пятна».

## Экспериментальные данные

### `processed/vacancies.csv`
Нормализованный набор вакансий, используемый в эксперименте.

### `processed/resumes_experiment.csv`
Контролируемый набор резюме для эксперимента.

В нём один и тот же кандидат представлен несколькими версиями, различающимися формой текста, но не базовым содержанием опыта.

Ключевые поля:
- `resume_id`
- `candidate_id`
- `version_type`
- `title`
- `candidate_text`
- `skills`
- `total_experience_years`
- `last_position`
- `domain`

## Основной workflow

### 1. Подготовить экспериментальные данные
Убедиться, что в проекте есть:
- `processed/vacancies.csv`
- `processed/resumes_experiment.csv`

### 2. Сгенерировать пары для разметки
```bash
py build_smart_labels_experiment.py
```

или

```bash
py build_smart_labels_experiment.py --config smart_labels_config_experiment.json
```

### 3. Подготовить Excel для разметки
```bash
py prepare_annotation_round.py
```

После запуска создаются:
- `annotations/labels_template_smart_merged.csv`
- `annotations/labels_template_smart_merged.xlsx`

### 4. Выполнить ручную разметку
Открыть файл:

```text
annotations/labels_template_smart_merged.xlsx
```

Заполняются поля:
- `annotator_1_label`
- `annotator_2_label`
- `final_label`
- `comment`

### 5. Завершить раунд и посчитать метрики
```bash
py finalize_annotation_round.py
```

После этого появляются:
- `annotations/labels_template_smart_filled.csv`
- `reports/metrics_by_vacancy.csv`
- `reports/ranked_pairs_scored.csv`
- `reports/label_distribution.csv`
- `reports/metrics_summary.json`
- `reports/metrics_report.txt`

## Шкала разметки

- `2` — релевантен;
- `1` — частично релевантен;
- `0` — нерелевантен.

## Что важно интерпретировать в эксперименте

В этом проекте важны не только общие метрики ранжирования, но и такие случаи, когда:

- кандидат содержательно релевантен;
- разные версии его резюме получают разный результат;
- одна версия попадает в top-k, а другая — нет;
- score и rank меняются из-за формы представления опыта.

Именно такие случаи рассматриваются как возможное проявление «слепого пятна».

## Шаблоны

Папка `templates/` хранит служебные шаблоны, которые можно держать в репозитории:

- `templates/annotation_template_blank.xlsx`

