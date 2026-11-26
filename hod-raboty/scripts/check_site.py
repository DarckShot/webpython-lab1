"""Verify the built or published site at the actual URL prefix."""

import argparse
import csv
import hashlib
import io
import json
import ssl
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree


def check(read, expected_commit=None):
    home = read("index.html").decode()
    assert "LAB1-REPRODUCIBLE-EXPERIMENT" in home, "контрольная строка отсутствует"
    assert "<math " in home and "<mfrac>" in home, "формула MathML отсутствует"
    page = read("results/index.html").decode()
    metadata = json.loads(read("results/build.json"))
    data = read("results/experiment.csv")
    assert hashlib.sha256(data).hexdigest() == metadata["data_sha256"], "неверный SHA-256"
    assert metadata["commit"] in page and metadata["built_at_utc"] in page, "не найдена метка сборки"
    assert metadata["dataset_version"] in page, "не найдена версия набора"
    if expected_commit:
        assert metadata["commit"] == expected_commit, f"ожидался коммит {expected_commit}, получен {metadata['commit']}"
        assert not metadata["uncommitted_experiment"], "публикуется незакоммиченный эксперимент"
    table = list(csv.DictReader(io.StringIO(read("results/summary.csv").decode())))
    assert len(table) >= 2 and "<table" in page, "итоговая таблица отсутствует"
    root = ElementTree.fromstring(read("results/chart.svg"))
    ns = {"svg": "http://www.w3.org/2000/svg"}
    assert len(root.findall(".//svg:circle", ns)) == len(table) * 2, "на графике отсутствуют точки"
    index = json.loads(read("search/search_index.json"))
    assert any("Результаты эксперимента" in entry["title"] for entry in index["docs"]), "результатов нет в поисковом индексе"
    assert "ru" in index["config"]["lang"], "русский язык поиска отключён"
    read("assets/lab.css")
    print(f"OK: {metadata['dataset_version']} · {metadata['commit']} · {len(table)} размеров · формула и индекс на месте", flush=True)


def remote_reader(base, bust):
    prefix = base.rstrip("/") + "/"
    context = ssl.create_default_context(cafile="/etc/ssl/cert.pem" if Path("/etc/ssl/cert.pem").exists() else None)

    def read(name):
        url = urljoin(prefix, name)
        if bust:
            url += f"?revision={bust}"
        request = Request(url, headers={"User-Agent": "webpython-lab1-check/1.0", "Cache-Control": "no-cache"})
        with urlopen(request, timeout=12, context=context) as response:
            assert response.status == 200, f"{url}: HTTP {response.status}"
            assert urlsplit(response.url).path.startswith(urlsplit(prefix).path), f"редирект вне префикса: {response.url}"
            print(f"HTTP 200 {name}", flush=True)
            return response.read()
    return read


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--site", type=Path)
    source.add_argument("--url")
    parser.add_argument("--commit")
    parser.add_argument("--attempts", type=int, default=1)
    args = parser.parse_args()
    if args.attempts < 1:
        parser.error("--attempts must be at least 1")
    reader = remote_reader(args.url, args.commit) if args.url else lambda name: (args.site / name).read_bytes()
    for attempt in range(args.attempts):
        try:
            check(reader, args.commit)
            break
        except (AssertionError, OSError, ValueError, KeyError, ElementTree.ParseError) as error:
            print(f"Проверка {attempt + 1}/{args.attempts}: {error}", file=sys.stderr, flush=True)
            if attempt + 1 == args.attempts:
                sys.exit(1)
            time.sleep(5)
