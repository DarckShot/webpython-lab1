"""Turn the versioned timings CSV into static research artifacts."""

import argparse
import csv
import hashlib
import html
import io
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "P3/data/experiment.csv"
OUTPUT = ROOT / "hod-raboty/docs/results"
CACHE = ROOT / ".cache/experiment"
FIELDS = ["version", "size", "trial", "baseline_ms", "optimized_ms"]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def calculate(raw_data):
    reader = csv.DictReader(io.StringIO(raw_data.decode("utf-8")))
    if reader.fieldnames != FIELDS:
        raise ValueError("ожидаются поля " + ", ".join(FIELDS))

    groups = defaultdict(list)
    versions = set()
    seen = set()
    for row in reader:
        try:
            version = row["version"]
            if None in row or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version or ""):
                raise ValueError("некорректная версия или лишнее поле")
            size, trial = int(row["size"]), int(row["trial"])
            timings = float(row["baseline_ms"]), float(row["optimized_ms"])
            if size < 1 or trial < 1 or not all(math.isfinite(x) and x > 0 for x in timings):
                raise ValueError("размер, повтор и обе длительности должны быть положительными")
            if (size, trial) in seen:
                raise ValueError("повторяется пара (размер, повтор)")
        except (TypeError, ValueError) as error:
            raise ValueError(f"строка {reader.line_num}: {error}") from error
        versions.add(version)
        seen.add((size, trial))
        groups[size].append(timings)
    if len(versions) != 1 or len(groups) < 2:
        raise ValueError("нужны одна версия датасета и минимум два размера")

    summary = []
    for size, samples in sorted(groups.items()):
        baseline, optimized = map(median, zip(*samples))
        speedup = baseline / optimized
        if not math.isfinite(speedup):
            raise ValueError(f"ускорение для N={size} выходит за числовой диапазон")
        summary.append((size, len(samples), baseline, optimized, speedup))
    return versions.pop(), summary


def chart(rows):
    left, right, top, bottom = 86, 654, 55, 270
    limit = max(max(row[2:4]) for row in rows)
    x = [left + (right - left) * index / (len(rows) - 1) for index in range(len(rows))]
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 340" role="img" aria-labelledby="title desc">',
             '<title id="title">Медианное время обработки</title>',
             '<desc id="desc">Две серии длительностей для размеров ' +
             html.escape(", ".join(str(row[0]) for row in rows)) + '.</desc>',
             '<rect width="720" height="340" rx="12" fill="#f6f8fa"/>',
             '<g font-family="system-ui,sans-serif" fill="#172832" font-size="13">',
             '<text x="86" y="30" font-size="17" font-weight="700">Медианное время, мс</text>']
    for step in range(5):
        y = bottom - (bottom - top) * step / 4
        value = limit * (step / 4)
        parts.append(f'<path d="M {left} {y} H {right}" stroke="#c6d1d9"/>')
        parts.append(f'<text x="{left - 13}" y="{y + 4}" text-anchor="end">{value:.4g}</text>')
    if len(rows) <= 8:
        for position, row in zip(x, rows):
            parts.append(f'<text x="{position:.2f}" y="292" text-anchor="middle">{row[0]}</text>')
    parts.append('<text x="370" y="321" text-anchor="middle">Размер входа N (категории)</text>')
    for column, label, color, dash, label_x in (
        (2, "Базовый", "#236193", "", 350),
        (3, "Оптимизированный", "#a83e1a", ' stroke-dasharray="8 4"', 465),
    ):
        y_values = [bottom - (bottom - top) * (row[column] / limit) for row in rows]
        points = " ".join(f"{px:.2f},{py:.2f}" for px, py in zip(x, y_values))
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="3"{dash}/>')
        parts.append(f'<path d="M {label_x} 25 h22" stroke="{color}" stroke-width="3"{dash}/>')
        parts.append(f'<text x="{label_x + 28}" y="30">{label}</text>')
        for px, py, row in zip(x, y_values, rows):
            parts.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="4" fill="{color}">'
                         f'<title>{label}, N={row[0]}: {row[column]:.6g} мс</title></circle>')
    return "\n".join([*parts, "</g></svg>"]) + "\n"


def render(raw_data):
    version, rows = calculate(raw_data)
    csv_output = io.StringIO()
    writer = csv.writer(csv_output, lineterminator="\n")
    writer.writerow(["size", "trials", "baseline_median_ms", "optimized_median_ms", "speedup"])
    writer.writerows(rows)
    table = ["| Размер N | Повторы | Базовый, мс | Оптимизированный, мс | Ускорение |",
             "| ---: | ---: | ---: | ---: | ---: |"]
    table.extend(f"| {n} | {k} | {base:.6g} | {opt:.6g} | {speedup:.4f}× |"
                 for n, k, base, opt, speedup in rows)
    return {
        "result.json": json.dumps({"dataset_version": version}, ensure_ascii=False),
        "summary.csv": csv_output.getvalue(),
        "chart.svg": chart(rows),
        "table.md": "\n".join(table),
    }


def cached_result(key, raw_data):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{key}.json"
    started = time.perf_counter()
    try:
        envelope = json.loads(path.read_bytes())
        payload = envelope["payload"]
        if envelope["sha256"] != digest(json.dumps(payload, sort_keys=True).encode()):
            raise ValueError("контрольная сумма кэша не совпадает")
        if set(payload) != {"result.json", "summary.csv", "chart.svg", "table.md"}:
            raise ValueError("неполный кэш")
        status = "HIT"
    except (OSError, ValueError, TypeError, KeyError):
        payload = render(raw_data)
        envelope = {"payload": payload, "sha256": digest(json.dumps(payload, sort_keys=True).encode())}
        atomic_write(path, json.dumps(envelope, ensure_ascii=False))
        status = "MISS"
    print(json.dumps({"cache": status, "result_ms": round((time.perf_counter() - started) * 1000, 3),
                      "key": key}), flush=True)
    return payload


def atomic_write(path, content):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     delete=False) as temporary:
        temporary.write(content)
    try:
        os.replace(temporary.name, path)
    finally:
        Path(temporary.name).unlink(missing_ok=True)


def source_revision():
    revision = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=ROOT,
                              text=True, capture_output=True)
    if revision.returncode == 0:
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all", "--", "P3/"], cwd=ROOT, text=True
        )
        return revision.stdout.strip(), bool(dirty)
    return "архив без Git или первый коммит ещё не создан", True


def prepare(checkout_only):
    raw_data = SOURCE.read_bytes()
    data_hash = digest(raw_data)
    code_hash = digest(Path(__file__).read_bytes())
    runtime = f"{sys.implementation.name} {sys.version.split()[0]}"
    key = digest(f"{data_hash}:{code_hash}:{runtime}".encode())
    payload = cached_result(key, raw_data)
    if checkout_only:
        return

    revision, dirty = source_revision()
    version = json.loads(payload["result.json"])["dataset_version"]
    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    metadata = {"commit": revision, "uncommitted_experiment": dirty, "built_at_utc": built_at,
                "dataset_version": version, "data_sha256": data_hash, "code_sha256": code_hash,
                "python": runtime, "cache_key": key}
    page = f"""# Результаты эксперимента

Версия набора данных **{version}**.

![График медианных длительностей базового и оптимизированного вариантов](chart.svg)

График показывает медиану каждого варианта по повторам при фиксированном размере входа.
Полные значения также приведены в таблице.

{payload["table.md"]}

[Скачать сводную таблицу CSV](summary.csv) · [Исходные данные CSV](experiment.csv)

## Метаданные сборки

- Коммит `{revision}`
- Незакоммиченные изменения эксперимента — {"да" if dirty else "нет"}
- Дата сборки UTC `{built_at}`
- SHA-256 данных `{data_hash}`
- SHA-256 расчётного скрипта `{code_hash}`

[Скачать метаданные JSON](build.json)
"""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    files = {"index.md": page, "build.json": json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
             "experiment.csv": raw_data.decode("utf-8"),
             "chart.svg": payload["chart.svg"], "summary.csv": payload["summary.csv"]}
    for name, contents in files.items():
        atomic_write(OUTPUT / name, contents)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compute-only", action="store_true", help="populate or measure the calculation cache")
    try:
        prepare(parser.parse_args().compute_only)
    except (OSError, ValueError) as error:
        parser.exit(1, f"experiment: {error}\n")
