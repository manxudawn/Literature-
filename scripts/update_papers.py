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
KEYWORDS = ROOT / "data" / "keywords.json"
BERLIN = ZoneInfo("Europe/Berlin")

# Search recent literature broadly, then rank locally.
LOOKBACK_DAYS = 45
FALLBACK_LOOKBACK_DAYS = 180
MAX_DAILY = 10
MAX_PER_TOPIC = 4
MAX_ARCHIVE = 320
MIN_SCORE = {"organic": 38, "gde": 33, "analysis": 34}

# Keep the scheduled job fast and resilient when OpenAlex is degraded.
OPENALEX_CONNECT_TIMEOUT = 4
OPENALEX_READ_TIMEOUT = 12
OPENALEX_MAX_CONSECUTIVE_FAILURES = 3
OPENALEX_REQUEST_PAUSE = 0.15
MAX_SEARCHES_PER_TOPIC = 3
FALLBACK_SEARCHES_PER_TOPIC = 1

API_STATS = {
    "ok": 0,
    "failed": 0,
    "consecutive_failed": 0,
    "circuit_open": False,
}


class OpenAlexRequestFailed(RuntimeError):
    pass


class OpenAlexCircuitOpen(RuntimeError):
    pass

TOPICS = {
    "organic": [
        "electrocarboxylation CO2",
        "organic electrocarboxylation carbon dioxide",
        "electrochemical carboxylation CO2",
        "reductive carboxylation carbon dioxide electrochemistry",
        "electrochemical CO2 fixation organic synthesis",
        "electrochemical hydrocarboxylation carbon dioxide",
        "carboxylative cyclization electrochemistry CO2",
        "nickel electrosynthesis CO2 carboxylation",
        "nickel mediated electrocarboxylation",
        "diene electrocarboxylation",
        "alkene electrocarboxylation",
        "CO2 incorporation electrosynthesis",
        "electrochemical carbon dioxide incorporation organic",
    ],
    "gde": [
        "gas diffusion electrode CO2 electrolysis",
        "gas diffusion layer CO2 electrolyzer",
        "zero gap CO2 electrolyzer gas diffusion electrode",
        "GDE flooding wetting CO2",
        "cathode flooding gas diffusion electrode",
        "operando gas diffusion electrode water management",
        "X-ray tomography gas diffusion electrode",
        "operando X-ray tomography electrolyzer electrode",
        "micro CT porous electrode catalyst layer",
        "microcomputed tomography electrochemical electrode",
        "3D reconstruction porous electrode tomography",
        "micro-CT segmentation gas diffusion electrode",
        "porous transport layer tomography electrolysis",
    ],
    "analysis": [
        "cyclic voltammetry coupled chemical reaction kinetics",
        "cyclic voltammetry EC ECE mechanism",
        "cyclic voltammetry reaction mechanism electrosynthesis",
        "nonaqueous reference electrode ferrocene DMF",
        "reference electrode calibration nonaqueous electrochemistry",
        "electrochemical impedance spectroscopy porous electrode",
        "EIS equivalent circuit porous electrode",
        "distribution of relaxation times electrochemical impedance",
        "rotating ring disk electrode selectivity mechanism",
        "RRDE peroxide selectivity alkaline",
        "electrochemical kinetics mass transport mechanism",
        "Tafel transfer coefficient electrocatalysis",
    ],
}

WEIGHTS = {
    "organic": {
        "electrocarboxylation": 42,
        "electrochemical carboxylation": 38,
        "reductive carboxylation": 36,
        "hydrocarboxylation": 30,
        "carboxylative": 22,
        "carboxylation": 17,
        "co2 fixation": 18,
        "carbon dioxide fixation": 18,
        "co2 incorporation": 20,
        "carbon dioxide incorporation": 20,
        "carbon dioxide": 9,
        " co2 ": 9,
        "electrosynthesis": 11,
        "electroreduction": 8,
        "nickel": 8,
        "diene": 8,
        "alkene": 6,
        "dmf": 5,
    },
    "gde": {
        "gas diffusion electrode": 35,
        "gas diffusion layer": 32,
        " gde ": 28,
        " gdl ": 25,
        "zero-gap": 15,
        "zero gap": 15,
        "micro-ct": 32,
        "micro ct": 32,
        "microcomputed tomography": 32,
        "x-ray tomography": 29,
        "x ray tomography": 29,
        "tomography": 15,
        "3d reconstruction": 19,
        "segmentation": 14,
        "flooding": 18,
        "wetting": 15,
        "water management": 12,
        "catalyst layer": 12,
        "porous electrode": 12,
        "porous transport layer": 15,
        "co2 electrolysis": 15,
        "electrolyzer": 8,
        "microstructure": 9,
        "operando": 5,
    },
    "analysis": {
        "cyclic voltammetry": 24,
        "voltammetric": 14,
        "coupled chemical reaction": 22,
        "ec mechanism": 25,
        "ece mechanism": 25,
        "reaction mechanism": 9,
        "kinetic": 9,
        "reference electrode": 27,
        "ferrocene": 18,
        "fc/fc+": 18,
        "nonaqueous": 14,
        "non-aqueous": 14,
        "dmf": 7,
        "electrochemical impedance spectroscopy": 23,
        "impedance spectroscopy": 18,
        "equivalent circuit": 12,
        "distribution of relaxation times": 18,
        "rotating ring-disk": 23,
        "rotating ring disk": 23,
        "rrde": 21,
        "transfer coefficient": 15,
        "mass transport": 9,
        "tafel": 10,
    },
}

# Strong penalties for recurring off-topic results. A paper can still survive if it
# contains very strong topic-specific evidence, except for the analysis title gate.
NEGATIVE_TERMS = {
    "direct methanol fuel cell": 70,
    " dmfc ": 70,
    "proton exchange membrane fuel cell": 55,
    "pem fuel cell": 55,
    "lithium-ion battery": 48,
    "lithium ion battery": 48,
    "sodium-ion battery": 48,
    "sodium ion battery": 48,
    "supercapacitor": 48,
    "photocatal": 24,
}

METHOD_HINTS = (
    "using ", "we used", "we employ", "we employed", "we perform", "we performed",
    "we measure", "we measured", "we characterize", "we characterized", "we investigate",
    "we investigated", "we analyze", "we analysed", "was measured", "were measured",
    "was characterized", "were characterized", "spectroscopy", "microscopy", "tomography",
    "voltammetry", "impedance", "electrolysis", "chronoamper", "chronopotent", "chromatograph",
    "hplc", "gc-ms", "gas chromatography", "mass spectrometry", "x-ray", "x ray", "simulation",
    "density functional theory", "dft", "finite element", "modeling", "modelling",
)
FINDING_HINTS = (
    "we found", "we show", "we demonstrate", "we reveal", "results show", "results indicate",
    "results demonstrate", "revealed", "showed", "found that", "increased", "decreased",
    "improved", "enhanced", "selectivity", "yield", "conversion", "faradaic efficiency",
    "stability", "suggests that", "indicates that", "achieved", "reached", "led to",
)


@dataclass
class Candidate:
    paper: dict
    rank: int


def http_session() -> requests.Session:
    retry = Retry(
        total=1,
        connect=1,
        read=1,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=False,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": "e-CarbonScope-literature-radar/2.0"})
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


def truncate(text: str, limit: int = 680) -> str:
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
                "electrochemical carboxylation", "reductive carboxylation", " carboxylation ",
                "hydrocarboxylation", "carboxylative", "co2 fixation", "carbon dioxide fixation",
                "co2 incorporation", "carbon dioxide incorporation",
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
            ("co2", "electroly", "electrode", "catalyst layer", "porous", "flooding", "wetting", "microstructure", "water management"),
        )
        return (gde and context) or (imaging and context)

    if topic == "analysis":
        if contains_any(
            title_n,
            ("direct methanol fuel cell", " dmfc ", "pem fuel cell", "battery", "supercapacitor"),
        ):
            return False
        method = contains_any(
            full,
            (
                "cyclic voltammetry", "voltammetric", "reference electrode", "ferrocene", "fc/fc+",
                "electrochemical impedance spectroscopy", "impedance spectroscopy", "equivalent circuit",
                "distribution of relaxation times", "rotating ring-disk", "rotating ring disk", "rrde",
                "transfer coefficient", "tafel",
            ),
        )
        context = contains_any(
            full,
            (
                "mechanism", "kinetic", "coupled chemical reaction", "calibration", "nonaqueous",
                "non-aqueous", "equivalent circuit", "mass transport", "electrosynthesis",
                "electrocatal", "porous electrode", "reference electrode", "selectivity",
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
    return min(99, 76 + max(0, round((raw - threshold) * 0.55)))


def best_topic(title: str, abstract: str) -> tuple[str, int, list[str]] | None:
    choices: list[tuple[int, str, list[str]]] = []
    for topic in TOPICS:
        if not hard_gate(topic, title, abstract):
            continue
        score, matched = raw_score(topic, title, abstract)
        if score >= MIN_SCORE[topic]:
            choices.append((score, topic, matched))
    if not choices:
        return None
    score, topic, matched = max(choices, key=lambda x: x[0])
    return topic, score, matched


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


def source_info(work: dict) -> tuple[str, list[str]]:
    primary = work.get("primary_location") or {}
    source = primary.get("source") or {}
    name = source.get("display_name") or "Unknown journal"
    issns = source.get("issn") or []
    if source.get("issn_l") and source.get("issn_l") not in issns:
        issns = [source.get("issn_l"), *issns]
    return name, [x for x in issns if x]


def build_paper(work: dict, topic: str, rank: int, matched: list[str], discovery_source: str) -> dict:
    title = (work.get("title") or "").strip()
    abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
    source, issns = source_info(work)
    authors = [
        ((a.get("author") or {}).get("display_name") or "").strip()
        for a in work.get("authorships", [])[:8]
    ]
    authors = [a for a in authors if a]
    doi = (work.get("doi") or "").strip()
    wid = (work.get("id") or "").split("/")[-1]
    if not wid:
        wid = re.sub(r"\W+", "-", title.lower()).strip("-")[:70]

    return {
        "id": wid,
        "doi": doi,
        "topic": topic,
        "badge": "Latest research",
        "read_minutes": read_minutes(abstract),
        "title": title,
        "journal": source,
        "journal_issn": issns,
        "year": work.get("publication_year"),
        "publication_date": work.get("publication_date") or "",
        "authors": authors,
        "summary_en": "",
        "methods_summary": "",
        "key_findings": "",
        "relevance_reason": "",
        "abstract": truncate(abstract, 6000),
        "tags": matched[:6],
        "score": display_score(topic, rank),
        "url": get_primary_url(work),
        "archive": False,
        "discovery_source": discovery_source,
        "_rank": rank,
    }


def _short_error(exc: Exception) -> str:
    text = re.sub(r"\s+", " ", str(exc)).strip()
    # urllib3 errors can contain the full query URL; keep Actions logs readable.
    if " with url:" in text:
        text = text.split(" with url:", 1)[0]
    return text[:240]


def openalex_get(params: dict) -> dict:
    if API_STATS["circuit_open"]:
        raise OpenAlexCircuitOpen("OpenAlex circuit breaker is open for this run")

    mailto = os.getenv("OPENALEX_MAILTO", "").strip()
    if mailto:
        params["mailto"] = mailto

    try:
        response = SESSION.get(
            "https://api.openalex.org/works",
            params=params,
            timeout=(OPENALEX_CONNECT_TIMEOUT, OPENALEX_READ_TIMEOUT),
        )
        response.raise_for_status()
        API_STATS["ok"] += 1
        API_STATS["consecutive_failed"] = 0
        time.sleep(OPENALEX_REQUEST_PAUSE)
        return response.json()
    except requests.RequestException as exc:
        API_STATS["failed"] += 1
        API_STATS["consecutive_failed"] += 1
        if API_STATS["consecutive_failed"] >= OPENALEX_MAX_CONSECUTIVE_FAILURES:
            API_STATS["circuit_open"] = True
        raise OpenAlexRequestFailed(_short_error(exc)) from exc


def fetch_query(topic: str, query: str, date_from: str, date_to: str) -> list[Candidate]:
    params = {
        "search": query,
        "filter": f"from_publication_date:{date_from},to_publication_date:{date_to},is_paratext:false",
        "sort": "publication_date:desc",
        "per-page": 50,
    }
    payload = openalex_get(params)
    candidates: list[Candidate] = []

    for work in payload.get("results", []):
        title = (work.get("title") or "").strip()
        if not title:
            continue
        abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
        if not hard_gate(topic, title, abstract):
            continue
        rank, matched = raw_score(topic, title, abstract)
        if rank < MIN_SCORE[topic]:
            continue
        candidates.append(Candidate(build_paper(work, topic, rank, matched, "topic-search"), rank))
    return candidates


def metric_records(metrics: dict) -> list[tuple[str, dict]]:
    return [(k, v) for k, v in metrics.items() if not k.startswith("_") and isinstance(v, dict)]


def watched_issns(metrics: dict) -> list[str]:
    values: list[str] = []
    for _, info in metric_records(metrics):
        if not info.get("watch", False):
            continue
        for issn in info.get("issn", []):
            if issn and issn not in values:
                values.append(issn)
    return values


def fetch_watchlist(metrics: dict, date_from: str, date_to: str) -> list[Candidate]:
    """Scan a broad set of high-value journals, then apply the same relevance gates.

    This is complementary to keyword search: it can recover papers whose abstract/title
    uses unfamiliar terminology but appears in a closely watched journal.
    """
    issns = watched_issns(metrics)
    candidates: list[Candidate] = []
    batch_size = 30

    for start in range(0, len(issns), batch_size):
        batch = issns[start : start + batch_size]
        try:
            params = {
                "filter": (
                    f"from_publication_date:{date_from},to_publication_date:{date_to},"
                    f"primary_location.source.issn:{'|'.join(batch)},is_paratext:false"
                ),
                "sort": "publication_date:desc",
                "per-page": 100,
            }
            payload = openalex_get(params)
            accepted = 0
            for work in payload.get("results", []):
                title = (work.get("title") or "").strip()
                if not title:
                    continue
                abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
                choice = best_topic(title, abstract)
                if not choice:
                    continue
                topic, rank, matched = choice
                candidates.append(Candidate(build_paper(work, topic, rank, matched, "journal-watchlist"), rank))
                accepted += 1
            print(f"journal watchlist | batch {start // batch_size + 1} | {accepted:2d} accepted")
        except OpenAlexCircuitOpen:
            print("OpenAlex circuit breaker opened during journal watchlist; stopping remote requests.", file=sys.stderr)
            break
        except Exception as exc:
            print(f"OpenAlex journal watchlist batch failed: {_short_error(exc)}", file=sys.stderr)
            if API_STATS["circuit_open"]:
                print("OpenAlex is repeatedly unavailable; skipping remaining watchlist batches.", file=sys.stderr)
                break

    return candidates


def configured_searches(keyword_config: dict) -> dict[str, list[str]]:
    """Return the editable search-query list from data/keywords.json.

    The three topic keys remain fixed so scoring/gating stays predictable. If the
    JSON is missing or malformed, the built-in TOPICS dictionary is used.
    """
    configured: dict[str, list[str]] = {}
    topics = keyword_config.get("topics", {}) if isinstance(keyword_config, dict) else {}

    for topic, fallback in TOPICS.items():
        raw = (topics.get(topic) or {}).get("keywords", [])
        if not isinstance(raw, list):
            raw = []
        seen: set[str] = set()
        cleaned: list[str] = []
        for item in raw:
            value = re.sub(r"\s+", " ", str(item or "")).strip()
            key = value.lower()
            if value and key not in seen:
                cleaned.append(value)
                seen.add(key)
        configured[topic] = cleaned if cleaned else list(fallback)

    return configured


def selected_queries_for_run(
    topic: str, queries: list[str], now: datetime, *, fallback: bool = False
) -> list[str]:
    """Use a small rotating subset instead of hammering OpenAlex with every keyword.

    User-added keywords (those not in the built-in default list) are searched first so
    they take effect immediately. Remaining slots rotate through the configured list
    across days, so the whole keyword library is still covered over time.
    """
    if not queries:
        return []

    limit = FALLBACK_SEARCHES_PER_TOPIC if fallback else MAX_SEARCHES_PER_TOPIC
    defaults = {normalize(q).strip() for q in TOPICS.get(topic, [])}
    custom = [q for q in queries if normalize(q).strip() not in defaults]

    picked: list[str] = []
    for q in custom:
        if q not in picked:
            picked.append(q)
        if len(picked) >= limit:
            return picked

    remaining = [q for q in queries if q not in picked]
    if not remaining:
        return picked

    # Stable daily rotation with a different offset per topic.
    offsets = {"organic": 0, "gde": 7, "analysis": 13}
    start = (now.date().toordinal() + offsets.get(topic, 0)) % len(remaining)
    rotated = remaining[start:] + remaining[:start]
    for q in rotated:
        if q not in picked:
            picked.append(q)
        if len(picked) >= limit:
            break
    return picked


def collect_candidates(
    now: datetime,
    days: int,
    metrics: dict,
    search_topics: dict[str, list[str]],
    *,
    fallback: bool = False,
) -> list[dict]:
    start = (now.date() - timedelta(days=days)).isoformat()
    end = now.date().isoformat()
    all_candidates: list[Candidate] = []

    for topic, configured_queries in search_topics.items():
        queries = selected_queries_for_run(topic, configured_queries, now, fallback=fallback)
        for query in queries:
            if API_STATS["circuit_open"]:
                break
            try:
                found = fetch_query(topic, query, start, end)
                all_candidates.extend(found)
                print(f"{topic:8s} | {query[:48]:48s} | {len(found):2d} accepted", flush=True)
            except OpenAlexCircuitOpen:
                break
            except Exception as exc:
                print(f"OpenAlex fetch failed [{topic}] {query}: {_short_error(exc)}", file=sys.stderr, flush=True)
        if API_STATS["circuit_open"]:
            print("OpenAlex circuit breaker open; stopping remaining keyword searches.", file=sys.stderr, flush=True)
            break

    # The expensive journal scan runs only on the normal window and only while the
    # API is healthy. The wide fallback intentionally skips it.
    if not fallback and not API_STATS["circuit_open"]:
        all_candidates.extend(fetch_watchlist(metrics, start, end))

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

    if len(selected) < limit:
        rest: list[dict] = []
        for papers in grouped.values():
            rest.extend(papers)
        rest.sort(key=lambda p: (p.get("_rank", 0), p.get("publication_date", "")), reverse=True)
        selected.extend(rest[: limit - len(selected)])

    return selected[:limit]


def make_english_digest(paper: dict) -> None:
    """Create deterministic English summaries from the OpenAlex abstract only.

    No language-model API is used. The wording remains close to the authors' abstract
    so the page avoids inventing unsupported methods or conclusions.
    """
    abstract = paper.get("abstract", "")
    sentences = sentence_split(abstract)
    method_sentences = select_sentences(abstract, METHOD_HINTS, 2)
    finding_sentences = select_sentences(abstract, FINDING_HINTS, 2)

    if abstract:
        overview: list[str] = []
        if sentences:
            overview.append(sentences[0])
        for sentence in finding_sentences:
            if sentence not in overview:
                overview.append(sentence)
            if len(overview) >= 2:
                break
        if len(overview) < 2 and len(sentences) > 1:
            overview.append(sentences[1])

        paper["summary_en"] = truncate(" ".join(overview), 560)
        paper["methods_summary"] = (
            truncate(" ".join(method_sentences), 520)
            if method_sentences
            else "The OpenAlex abstract does not state the experimental or analytical methods in enough detail."
        )
        paper["key_findings"] = (
            truncate(" ".join(finding_sentences), 560)
            if finding_sentences
            else truncate(sentences[-1], 560) if sentences else "No reliable conclusion could be extracted from the available abstract."
        )
    else:
        paper["summary_en"] = "No abstract was available from OpenAlex; open the original article for the full study summary."
        paper["methods_summary"] = "Methods cannot be extracted reliably because the abstract is unavailable."
        paper["key_findings"] = "Key findings cannot be extracted reliably because the abstract is unavailable."

    tags = ", ".join(paper.get("tags", [])[:4]) or "topic keywords"
    topic = paper.get("topic")
    if topic == "organic":
        paper["relevance_reason"] = f"Matched {tags}; directly relevant to CO2 electrocarboxylation, non-aqueous electrosynthesis, or Ni-mediated carbon incorporation."
    elif topic == "gde":
        paper["relevance_reason"] = f"Matched {tags}; relevant to GDE flooding/wetting, porous-electrode transport, or Micro-CT / tomography structure analysis."
    else:
        paper["relevance_reason"] = f"Matched {tags}; relevant to CV, EIS, reference-electrode calibration, mass transport, selectivity, or electrochemical mechanism analysis."
    paper["summary_mode"] = "abstract-extraction"


def load_json(path: Path, default: dict) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"Warning: could not read {path}: {exc}", file=sys.stderr)
        return default


def metric_for(journal: str, issns: list[str], metrics: dict) -> dict:
    norm_issns = {str(x).strip().upper() for x in (issns or []) if x}
    journal_n = normalize(journal).strip()

    # ISSN is the most reliable match because OpenAlex journal names can vary.
    for _, info in metric_records(metrics):
        metric_issns = {str(x).strip().upper() for x in info.get("issn", []) if x}
        if norm_issns and norm_issns.intersection(metric_issns):
            return info

    # Then match the canonical title or an alias.
    for title, info in metric_records(metrics):
        names = [title, *info.get("aliases", [])]
        for name in names:
            if normalize(name).strip() == journal_n:
                return info

    # Finally allow conservative containment for publisher title variants.
    if journal_n:
        for title, info in metric_records(metrics):
            for name in [title, *info.get("aliases", [])]:
                candidate = normalize(name).strip()
                if len(candidate) >= 8 and (candidate in journal_n or journal_n in candidate):
                    return info
    return {}


def add_metrics(paper: dict, metrics: dict) -> None:
    metric = metric_for(paper.get("journal", ""), paper.get("journal_issn", []), metrics)
    paper["impact_factor"] = metric.get("impact_factor")
    paper["quartile"] = metric.get("quartile", "JCR —")
    if metric.get("impact_factor"):
        paper["metric_year"] = "2025 JIF"
    else:
        paper.pop("metric_year", None)


def recency_badge(publication_date: str, now: datetime) -> str:
    try:
        age = (now.date() - date.fromisoformat(publication_date)).days
    except (TypeError, ValueError):
        return "Selected research"
    if age <= 14:
        return "Latest research"
    if age <= 60:
        return "Recent research"
    return "Recent pick"


def main() -> None:
    now = datetime.now(BERLIN)
    old = load_json(DATA, {"papers": [], "trackers": []})
    metrics = load_json(METRICS, {})
    keyword_config = load_json(KEYWORDS, {"topics": {}})
    search_topics = configured_searches(keyword_config)
    old_papers = old.get("papers") if isinstance(old.get("papers"), list) else []

    # Refresh metrics for the full archive whenever the local journal library changes.
    for paper in old_papers:
        paper["archive"] = True
        add_metrics(paper, metrics)
        # Convert legacy empty/fallback records into the API-free English format.
        if paper.get("abstract") and not paper.get("summary_en"):
            make_english_digest(paper)

    old_keys = {canonical_key(p) for p in old_papers}
    old_ids = {str(p.get("id", "")) for p in old_papers}

    candidates = collect_candidates(now, LOOKBACK_DAYS, metrics, search_topics, fallback=False)
    unseen = [
        p for p in candidates
        if canonical_key(p) not in old_keys and str(p.get("id", "")) not in old_ids
    ]

    # OpenAlex indexing can lag. Broaden the window if today's strict scan is sparse.
    if len(unseen) < 4:
        wider = collect_candidates(now, FALLBACK_LOOKBACK_DAYS, metrics, search_topics, fallback=True)
        merged: dict[str, dict] = {canonical_key(p): p for p in unseen}
        for paper in wider:
            key = canonical_key(paper)
            if key not in old_keys and str(paper.get("id", "")) not in old_ids:
                current = merged.get(key)
                if current is None or paper.get("_rank", 0) > current.get("_rank", 0):
                    merged[key] = paper
        unseen = list(merged.values())

    attempts = API_STATS["ok"] + API_STATS["failed"]
    failure_ratio = (API_STATS["failed"] / attempts) if attempts else 0.0
    if API_STATS["circuit_open"] or (attempts >= 3 and failure_ratio > 0.50):
        print(
            "OpenAlex is currently unstable "
            f"(ok={API_STATS['ok']}, failed={API_STATS['failed']}). "
            "Preserving the existing papers.json instead of publishing an incomplete/empty digest.",
            file=sys.stderr,
            flush=True,
        )
        return

    fresh = select_balanced(unseen, MAX_DAILY)

    for paper in fresh:
        make_english_digest(paper)
        paper["badge"] = recency_badge(paper.get("publication_date", ""), now)
        add_metrics(paper, metrics)
        paper["first_seen"] = now.isoformat(timespec="seconds")
        paper.pop("_rank", None)

    fresh_keys = {canonical_key(p) for p in fresh}
    archive = [p for p in old_papers if canonical_key(p) not in fresh_keys][:MAX_ARCHIVE]

    match_score = round(sum(int(p.get("score", 0)) for p in fresh) / len(fresh)) if fresh else 0
    high_count = sum(1 for p in fresh if int(p.get("score", 0)) >= 88)

    payload = {
        "brief_date": now.strftime("%Y · %m · %d"),
        "last_updated": now.isoformat(timespec="minutes"),
        "summary_mode": "English abstract extraction; no AI/API required",
        "journal_metric_snapshot": "2025 JIF / JCR quartile, curated 2026-09-30",
        "keyword_count": sum(len(v) for v in search_topics.values()),
        "keyword_config_updated": keyword_config.get("updated_at", ""),
        "match_score": match_score,
        "trackers": old.get("trackers", []),
        "papers": fresh + archive,
    }

    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        f"Radar updated at {now.isoformat(timespec='minutes')} | "
        f"fresh={len(fresh)} | high={high_count} | archive={len(archive)} | "
        f"journal-library={len(metric_records(metrics))} | "
        f"keywords={sum(len(v) for v in search_topics.values())} | "
        f"openalex-ok={API_STATS['ok']} | openalex-failed={API_STATS['failed']}"
    )


if __name__ == "__main__":
    main()
