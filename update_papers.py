from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "papers.json"
METRICS = ROOT / "data" / "journal_metrics.json"
BERLIN = ZoneInfo("Europe/Berlin")

# Daily radar settings.
LOOKBACK_DAYS = 30
FALLBACK_LOOKBACK_DAYS = 120
MAX_DAILY = 8
MAX_PER_TOPIC = 3
MIN_SCORE = {"organic": 42, "gde": 36, "analysis": 38}
MAX_ARCHIVE = 240

TOPICS = {
    "organic": [
        "electrocarboxylation CO2",
        "electrochemical carboxylation carbon dioxide",
        "reductive carboxylation CO2 electrochemistry",
        "electrochemical CO2 fixation organic synthesis",
        "nickel electrosynthesis CO2 carboxylation",
        "diene electrocarboxylation",
        "alkene electrocarboxylation CO2",
    ],
    "gde": [
        "gas diffusion electrode CO2 electrolysis",
        "gas diffusion layer CO2 electrolyzer",
        "GDE flooding wetting CO2",
        "X-ray tomography gas diffusion electrode",
        "micro CT porous electrode catalyst layer",
        "3D reconstruction tomography porous electrode",
        "micro-CT segmentation gas diffusion electrode",
    ],
    "analysis": [
        "cyclic voltammetry coupled chemical reaction kinetics",
        "cyclic voltammetry reaction mechanism electrosynthesis",
        "nonaqueous reference electrode ferrocene DMF",
        "electrochemical impedance spectroscopy porous electrode",
        "rotating ring disk electrode mechanism selectivity",
        "electrochemical kinetics mass transport mechanism",
    ],
}

WEIGHTS = {
    "organic": {
        "electrocarboxylation": 40,
        "electrochemical carboxylation": 36,
        "reductive carboxylation": 34,
        "carboxylation": 17,
        "co2 fixation": 18,
        "carbon dioxide fixation": 18,
        "carbon dioxide": 10,
        " co2 ": 10,
        "electrosynthesis": 10,
        "electroreduction": 8,
        "nickel": 8,
        "diene": 8,
        "alkene": 6,
        "dmf": 5,
    },
    "gde": {
        "gas diffusion electrode": 34,
        "gas diffusion layer": 31,
        " gde ": 28,
        " gdl ": 24,
        "micro-ct": 31,
        "micro ct": 31,
        "microcomputed tomography": 31,
        "x-ray tomography": 28,
        "x ray tomography": 28,
        "tomography": 14,
        "3d reconstruction": 18,
        "segmentation": 13,
        "flooding": 17,
        "wetting": 14,
        "catalyst layer": 12,
        "porous electrode": 11,
        "co2 electrolysis": 15,
        "electrolyzer": 8,
        "microstructure": 8,
    },
    "analysis": {
        "cyclic voltammetry": 24,
        "voltammetric": 14,
        "coupled chemical reaction": 21,
        "ec mechanism": 24,
        "ece mechanism": 24,
        "reaction mechanism": 9,
        "kinetic": 9,
        "reference electrode": 26,
        "ferrocene": 18,
        "fc/fc+": 18,
        "nonaqueous": 13,
        "non-aqueous": 13,
        "dmf": 7,
        "electrochemical impedance spectroscopy": 22,
        "impedance spectroscopy": 17,
        "equivalent circuit": 11,
        "rotating ring-disk": 22,
        "rotating ring disk": 22,
        "rrde": 20,
        "transfer coefficient": 15,
        "mass transport": 8,
        "tafel": 9,
    },
}

# These terms are not universally irrelevant, but they are common false positives for
# this specific radar. Strong method matches in the GDE/CT topic are still allowed.
NEGATIVE_TERMS = {
    "direct methanol fuel cell": 60,
    " dmfc ": 60,
    "proton exchange membrane fuel cell": 50,
    "pem fuel cell": 50,
    "lithium-ion battery": 45,
    "lithium ion battery": 45,
    "sodium-ion battery": 45,
    "sodium ion battery": 45,
    "supercapacitor": 45,
    "photocatal": 25,
}

METHOD_HINTS = (
    "using ", "we used", "we employed", "we performed", "we measured",
    "we characterized", "we investigated", "we analyzed", "we analysed",
    "was measured", "were measured", "was characterized", "were characterized",
    "spectroscopy", "microscopy", "tomography", "voltammetry", "impedance",
    "electrolysis", "chronoamper", "chromatograph", "hplc", "gc-ms", "gc ",
)
FINDING_HINTS = (
    "we found", "we show", "we demonstrate", "we reveal", "results show",
    "results indicate", "results demonstrate", "revealed", "showed", "found that",
    "increased", "decreased", "improved", "enhanced", "selectivity", "yield",
    "conversion", "faradaic efficiency", "stability", "suggests that", "indicates that",
)


@dataclass
class Candidate:
    paper: dict
    rank: int


def http_session() -> requests.Session:
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=0.7,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


SESSION = http_session()


def reconstruct_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    words: list[tuple[int, str]] = []
    for word, positions in inv.items():
        for pos in positions:
            words.append((pos, word))
    return " ".join(word for _, word in sorted(words))


def normalize(text: str) -> str:
    text = (text or "").lower().replace("₂", "2")
    text = re.sub(r"[\u2010-\u2015]", "-", text)
    text = re.sub(r"\s+", " ", text)
    return f" {text.strip()} "


def contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def sentence_split(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(])", text)
    return [p.strip() for p in parts if len(p.strip()) >= 25]


def select_sentences(abstract: str, hints: tuple[str, ...], limit: int = 2) -> list[str]:
    sentences = sentence_split(abstract)
    scored: list[tuple[int, int, str]] = []
    for i, sentence in enumerate(sentences):
        low = sentence.lower()
        score = sum(1 for hint in hints if hint in low)
        if score:
            scored.append((score, -i, sentence))
    scored.sort(reverse=True)
    return [x[2] for x in scored[:limit]]


def truncate(text: str, limit: int = 620) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def hard_gate(topic: str, title: str, abstract: str) -> bool:
    title_n = normalize(title)
    full = normalize(title + " " + abstract)

    if topic == "organic":
        if "electrocarboxylation" in full:
            return True
        carbox = contains_any(
            full,
            (
                "electrochemical carboxylation",
                "reductive carboxylation",
                " carboxylation ",
                "co2 fixation",
                "carbon dioxide fixation",
            ),
        )
        co2 = contains_any(full, (" co2 ", "carbon dioxide"))
        electro = contains_any(full, ("electrochem", "electrosynth", "electroreduc", "cathod"))
        return carbox and co2 and electro

    if topic == "gde":
        gde = contains_any(full, ("gas diffusion electrode", "gas diffusion layer", " gde ", " gdl "))
        imaging = contains_any(
            full,
            ("micro-ct", "micro ct", "microcomputed tomography", "x-ray tomography", "x ray tomography", "tomography"),
        )
        context = contains_any(
            full,
            ("co2", "electroly", "electrode", "catalyst layer", "porous", "flooding", "wetting", "microstructure"),
        )
        return (gde and context) or (imaging and context)

    if topic == "analysis":
        # Reject common application papers that only happen to mention CV/EIS.
        if contains_any(
            title_n,
            ("direct methanol fuel cell", " dmfc ", "pem fuel cell", "battery", "supercapacitor"),
        ):
            return False
        method = contains_any(
            full,
            (
                "cyclic voltammetry", "voltammetric", "reference electrode", "ferrocene",
                "fc/fc+", "electrochemical impedance spectroscopy", "impedance spectroscopy",
                "rotating ring-disk", "rotating ring disk", "rrde", "transfer coefficient",
            ),
        )
        context = contains_any(
            full,
            (
                "mechanism", "kinetic", "coupled chemical reaction", "calibration", "nonaqueous",
                "non-aqueous", "equivalent circuit", "mass transport", "electrosynthesis",
                "electrocatal", "porous electrode", "reference electrode",
            ),
        )
        return method and context

    return False


def raw_score(topic: str, title: str, abstract: str) -> tuple[int, list[str]]:
    title_n = normalize(title)
    full = normalize(title + " " + abstract)
    score = 0
    matched: list[str] = []

    for term, weight in WEIGHTS[topic].items():
        if term in full:
            score += weight
            matched.append(term.strip())
            if term in title_n:
                score += max(4, round(weight * 0.45))

    # Prefer papers with a usable abstract because method/conclusion extraction is better.
    if len(abstract) > 250:
        score += 5
    if contains_any(title_n, ("review", "perspective", "tutorial", "protocol")):
        score += 4

    if topic != "gde":
        for term, penalty in NEGATIVE_TERMS.items():
            if term in full:
                score -= penalty

    return max(0, score), list(dict.fromkeys(matched))


def display_score(topic: str, raw: int) -> int:
    threshold = MIN_SCORE[topic]
    # Accepted papers start at 76 and approach 99 for very strong matches.
    return min(99, 76 + max(0, round((raw - threshold) * 0.55)))


def canonical_key(paper: dict) -> str:
    doi = (paper.get("doi") or "").lower().strip()
    if doi:
        return doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
    title = re.sub(r"\W+", " ", paper.get("title", "").lower()).strip()
    return title[:220]


def read_minutes(abstract: str) -> int:
    words = len((abstract or "").split())
    return max(6, min(18, round(words / 190) + 5)) if words else 8


def get_primary_url(work: dict) -> str:
    doi = work.get("doi") or ""
    if doi:
        return doi
    primary = work.get("primary_location") or {}
    return primary.get("landing_page_url") or work.get("id") or "#"


def fetch_query(topic: str, query: str, date_from: str, date_to: str) -> list[Candidate]:
    params = {
        "search": query,
        "filter": f"from_publication_date:{date_from},to_publication_date:{date_to},is_paratext:false",
        "sort": "publication_date:desc",
        "per-page": 50,
    }
    mailto = os.getenv("OPENALEX_MAILTO", "").strip()
    if mailto:
        params["mailto"] = mailto

    response = SESSION.get("https://api.openalex.org/works", params=params, timeout=35)
    response.raise_for_status()

    candidates: list[Candidate] = []
    for work in response.json().get("results", []):
        title = (work.get("title") or "").strip()
        if not title:
            continue
        abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
        if not hard_gate(topic, title, abstract):
            continue

        rank, matched = raw_score(topic, title, abstract)
        if rank < MIN_SCORE[topic]:
            continue

        primary = work.get("primary_location") or {}
        source = (primary.get("source") or {}).get("display_name") or "Unknown journal"
        authors = [
            ((a.get("author") or {}).get("display_name") or "").strip()
            for a in work.get("authorships", [])[:8]
        ]
        authors = [a for a in authors if a]
        doi = (work.get("doi") or "").strip()
        wid = (work.get("id") or "").split("/")[-1]
        if not wid:
            wid = re.sub(r"\W+", "-", title.lower()).strip("-")[:70]

        paper = {
            "id": wid,
            "doi": doi,
            "topic": topic,
            "badge": "最新研究",
            "read_minutes": read_minutes(abstract),
            "title": title,
            "journal": source,
            "year": work.get("publication_year"),
            "publication_date": work.get("publication_date") or "",
            "authors": authors,
            "summary_zh": "",
            "methods_summary": "",
            "key_findings": "",
            "relevance_reason": "",
            "abstract": truncate(abstract, 5000),
            "tags": matched[:5],
            "score": display_score(topic, rank),
            "url": get_primary_url(work),
            "archive": False,
            "_rank": rank,
        }
        candidates.append(Candidate(paper=paper, rank=rank))
    return candidates


def collect_candidates(now: datetime, days: int) -> list[dict]:
    start = (now.date() - timedelta(days=days)).isoformat()
    end = now.date().isoformat()
    all_candidates: list[Candidate] = []

    for topic, queries in TOPICS.items():
        for query in queries:
            try:
                found = fetch_query(topic, query, start, end)
                all_candidates.extend(found)
                print(f"{topic:8s} | {query[:44]:44s} | {len(found):2d} accepted")
            except Exception as exc:
                print(f"OpenAlex fetch failed [{topic}] {query}: {exc}", file=sys.stderr)

    # Dedupe across queries/topics. Keep the strongest assignment.
    best: dict[str, Candidate] = {}
    for candidate in all_candidates:
        key = canonical_key(candidate.paper)
        current = best.get(key)
        if current is None or candidate.rank > current.rank:
            best[key] = candidate

    return [c.paper for c in best.values()]


def select_balanced(candidates: list[dict], limit: int = MAX_DAILY) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for topic in TOPICS:
        grouped[topic] = sorted(
            [p for p in candidates if p.get("topic") == topic],
            key=lambda p: (p.get("_rank", 0), p.get("publication_date", "")),
            reverse=True,
        )

    selected: list[dict] = []
    counts = {topic: 0 for topic in TOPICS}

    # First make sure no topic monopolizes the digest.
    while len(selected) < limit:
        added = False
        for topic in TOPICS:
            if len(selected) >= limit:
                break
            if counts[topic] >= MAX_PER_TOPIC or not grouped[topic]:
                continue
            selected.append(grouped[topic].pop(0))
            counts[topic] += 1
            added = True
        if not added:
            break

    # Fill spare positions with the best remaining papers.
    if len(selected) < limit:
        rest = []
        for papers in grouped.values():
            rest.extend(papers)
        rest.sort(key=lambda p: (p.get("_rank", 0), p.get("publication_date", "")), reverse=True)
        selected.extend(rest[: limit - len(selected)])

    return selected[:limit]


def fallback_fields(paper: dict) -> None:
    abstract = paper.get("abstract", "")
    method_sentences = select_sentences(abstract, METHOD_HINTS, 2)
    finding_sentences = select_sentences(abstract, FINDING_HINTS, 2)
    sentences = sentence_split(abstract)

    if not method_sentences and sentences:
        method_sentences = sentences[:1]
    if not finding_sentences and len(sentences) > 1:
        finding_sentences = sentences[-2:]

    if abstract:
        paper["summary_zh"] = "摘要提要（未启用 AI 中文总结）：" + truncate(" ".join(sentences[:2]), 420)
        paper["methods_summary"] = "原文摘要中的方法信息：" + truncate(" ".join(method_sentences), 420)
        paper["key_findings"] = "原文摘要中的主要结果：" + truncate(" ".join(finding_sentences), 420)
    else:
        paper["summary_zh"] = "OpenAlex 未提供该论文摘要，请打开原文查看完整内容。"
        paper["methods_summary"] = "摘要不可用，无法可靠提取研究方法。"
        paper["key_findings"] = "摘要不可用，无法可靠提取重点结论。"

    topic = paper.get("topic")
    tags = "、".join(paper.get("tags", [])[:4]) or "核心关键词"
    if topic == "organic":
        paper["relevance_reason"] = f"命中 {tags}，与 CO₂ 电羧化、非水电合成或 Ni 相关体系直接相关。"
    elif topic == "gde":
        paper["relevance_reason"] = f"命中 {tags}，可用于 GDE 润湿/淹没、孔结构或 Micro-CT 三维结构—性能分析。"
    else:
        paper["relevance_reason"] = f"命中 {tags}，可用于 CV、EIS、参比校准、传质或动力学/机理分析。"


def parse_json_object(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def enrich_with_openai(papers: list[dict]) -> None:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("OPENAI_API_KEY not set: using abstract sentence extraction fallback.")
        for paper in papers:
            fallback_fields(paper)
        return

    try:
        from openai import OpenAI
    except ImportError:
        print("openai package unavailable; using fallback.", file=sys.stderr)
        for paper in papers:
            fallback_fields(paper)
        return

    client = OpenAI(api_key=api_key)
    model = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")

    for paper in papers:
        abstract = paper.get("abstract", "")
        if not abstract:
            fallback_fields(paper)
            continue

        prompt = f"""你是一个科研文献筛选助手。只允许依据给出的论文标题和摘要，不得补充摘要里没有的信息。

请返回一个 JSON 对象，且只返回 JSON。字段必须为：
- summary_zh: 80-140 字中文概述，说明研究问题与总体内容。
- methods_summary: 60-140 字中文研究方法总结，写清实验/表征/分析方法；摘要未说明的内容要明确写“摘要未说明”。
- key_findings: 60-140 字中文重点结论，优先写可量化结果、趋势和作者结论；不得猜测。
- relevance_reason: 40-90 字中文，说明为什么与以下方向之一相关：CO2 electrocarboxylation / Ni-DMF electrosynthesis / GDE flooding-wetting / Micro-CT reconstruction / electrochemical mechanism analysis。
- tags: 3-5 个简短标签的 JSON 数组。

论文标题：{paper['title']}
期刊：{paper.get('journal', '')}
摘要：{abstract[:5000]}
"""
        try:
            response = client.responses.create(
                model=model,
                input=prompt,
                reasoning={"effort": "low"},
                max_output_tokens=900,
            )
            obj = parse_json_object(response.output_text)
            paper["summary_zh"] = truncate(str(obj.get("summary_zh", "")), 520)
            paper["methods_summary"] = truncate(str(obj.get("methods_summary", "")), 520)
            paper["key_findings"] = truncate(str(obj.get("key_findings", "")), 520)
            paper["relevance_reason"] = truncate(str(obj.get("relevance_reason", "")), 360)
            tags = obj.get("tags")
            if isinstance(tags, list):
                paper["tags"] = [str(x)[:40] for x in tags[:5]]
            if not all(paper.get(k) for k in ("summary_zh", "methods_summary", "key_findings", "relevance_reason")):
                fallback = paper.copy()
                fallback_fields(fallback)
                for key in ("summary_zh", "methods_summary", "key_findings", "relevance_reason"):
                    if not paper.get(key):
                        paper[key] = fallback[key]
        except Exception as exc:
            print(f"OpenAI enrichment failed for {paper.get('id')}: {exc}", file=sys.stderr)
            fallback_fields(paper)
        time.sleep(0.15)


def load_json(path: Path, default: dict) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"Warning: could not read {path}: {exc}", file=sys.stderr)
        return default


def metric_for(journal: str, metrics: dict) -> dict:
    if journal in metrics:
        return metrics[journal]
    jn = journal.lower().strip()
    for key, value in metrics.items():
        if key.lower().strip() == jn:
            return value
    return {}


def add_metrics(paper: dict, metrics: dict) -> None:
    metric = metric_for(paper.get("journal", ""), metrics)
    paper["impact_factor"] = metric.get("impact_factor")
    paper["quartile"] = metric.get("quartile", "Q —")


def recency_badge(publication_date: str, now: datetime) -> str:
    try:
        age = (now.date() - date.fromisoformat(publication_date)).days
    except (TypeError, ValueError):
        return "精选研究"
    if age <= 14:
        return "最新研究"
    if age <= 45:
        return "近期研究"
    return "近期精选"


def main() -> None:
    now = datetime.now(BERLIN)
    old = load_json(DATA, {"papers": [], "trackers": []})
    metrics = load_json(METRICS, {})
    old_papers = old.get("papers") if isinstance(old.get("papers"), list) else []

    # Archive the previous digest, preserving every other field and browser-facing behavior.
    for paper in old_papers:
        paper["archive"] = True

    old_keys = {canonical_key(p) for p in old_papers}
    old_ids = {str(p.get("id", "")) for p in old_papers}

    candidates = collect_candidates(now, LOOKBACK_DAYS)
    unseen = [
        p for p in candidates
        if canonical_key(p) not in old_keys and str(p.get("id", "")) not in old_ids
    ]

    # OpenAlex can index papers a little late. If today's 30-day window contains very
    # few unseen papers, search a wider window for high-relevance papers not pushed before.
    if len(unseen) < 3:
        wider = collect_candidates(now, FALLBACK_LOOKBACK_DAYS)
        merged: dict[str, dict] = {canonical_key(p): p for p in unseen}
        for p in wider:
            key = canonical_key(p)
            if key not in old_keys and str(p.get("id", "")) not in old_ids:
                existing = merged.get(key)
                if existing is None or p.get("_rank", 0) > existing.get("_rank", 0):
                    merged[key] = p
        unseen = list(merged.values())

    fresh = select_balanced(unseen, MAX_DAILY)
    enrich_with_openai(fresh)

    for paper in fresh:
        paper["badge"] = recency_badge(paper.get("publication_date", ""), now)
        add_metrics(paper, metrics)
        paper["first_seen"] = now.isoformat(timespec="seconds")
        paper.pop("_rank", None)

    fresh_keys = {canonical_key(p) for p in fresh}
    archive: list[dict] = []
    for paper in old_papers:
        if canonical_key(paper) not in fresh_keys:
            archive.append(paper)
    archive = archive[:MAX_ARCHIVE]

    match_score = round(sum(int(p.get("score", 0)) for p in fresh) / len(fresh)) if fresh else 0
    high_count = sum(1 for p in fresh if int(p.get("score", 0)) >= 88)

    payload = {
        "brief_date": now.strftime("%Y · %m · %d"),
        "last_updated": now.isoformat(timespec="minutes"),
        "match_score": match_score,
        "trackers": old.get("trackers", []),
        "papers": fresh + archive,
    }

    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        f"Radar updated at {now.isoformat(timespec='minutes')} | "
        f"fresh={len(fresh)} | high={high_count} | archive={len(archive)}"
    )


if __name__ == "__main__":
    main()
