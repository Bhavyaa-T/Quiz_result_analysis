#!/usr/bin/env python3
"""
Analyze quiz results and request a grounded semantic analysis from Perplexity.

Inputs
- quizzes.csv: columns [Quiz Link ID, Topic Name, Topic URL, Questions JSON, Created At]
- results.csv: columns [Result ID, Session ID, Quiz Link ID, PKI Score, Perceptions JSON, Created At]

Notes
- Perceptions JSON contains entries with questionId, userGuessValue, actualValue, timeToGuess, questionText.
- Questions JSON provides question metadata and sources. actualValue in quizzes.csv may be unreliable; use actualValue from results.csv.

Output
- Creates an `analyses/<result_id>/` folder per result with:
  - prompt.json: the constructed payload sent to Perplexity
  - response.json: the raw API response (if API key provided)
  - analysis.md: extracted assistant message content (if present)

Perplexity SDK
- Uses the official `perplexity` Python SDK (imported as `from perplexity import Perplexity`).
- Auth: API key in PPLX_API_KEY (or PERPLEXITY_API_KEY).
- Model: default "sonar-pro" (override via --model).

Usage
  python analyze_results_with_perplexity.py --quizzes quizzes.csv --results results.csv \
      --output-dir analyses --model sonar-pro --limit 0

If no API key is set, the script will not call the API and will write prompt.json only.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple





@dataclass
class Source:
    name: Optional[str]
    url: str


@dataclass
class QuestionMeta:
    id: str
    question: str
    category: Optional[str]
    sources: List[Source] = field(default_factory=list)


def load_quizzes(path: Path) -> Dict[str, Dict[str, QuestionMeta]]:
    """Load quizzes.csv and return mapping: quiz_link_id -> { question_id -> QuestionMeta }"""
    quizzes: Dict[str, Dict[str, QuestionMeta]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row:
                continue
            quiz_link_id = (row.get("Quiz Link ID") or "").strip()
            if not quiz_link_id:
                continue
            qjson_raw = row.get("Questions JSON") or ""
            qjson_str = qjson_raw.strip()
            if not qjson_str:
                continue
            try:
                # csv module de-escapes doubled quotes; expect valid JSON string here
                qitems = json.loads(qjson_str)
            except json.JSONDecodeError:
                # Try a lenient fallback: replace doubled quotes if still present
                qitems = json.loads(qjson_str.replace('""', '"'))

            qmap: Dict[str, QuestionMeta] = {}
            for item in qitems:
                qid = str(item.get("id") or item.get("questionId") or "").strip()
                if not qid:
                    continue
                question = str(item.get("question") or item.get("questionText") or "").strip()
                category = (item.get("category") or None)
                sources_list: List[Source] = []
                for s in (item.get("sources") or []):
                    url = str(s.get("url") or "").strip()
                    if not url:
                        continue
                    name = (s.get("name") or None)
                    sources_list.append(Source(name=name, url=url))
                qmap[qid] = QuestionMeta(id=qid, question=question, category=category, sources=sources_list)
            quizzes[quiz_link_id] = qmap
    return quizzes


def load_results(path: Path) -> List[Dict[str, Any]]:
    """Load results.csv rows as dicts with parsed perceptions list."""
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row:
                continue
            # Normalize and parse perceptions JSON
            pjson_raw = row.get("Perceptions JSON") or ""
            pjson_str = pjson_raw.strip()
            if pjson_str:
                try:
                    perceptions = json.loads(pjson_str)
                except json.JSONDecodeError:
                    perceptions = json.loads(pjson_str.replace('""', '"'))
            else:
                perceptions = []

            norm = {
                "result_id": (row.get("Result ID") or "").strip(),
                "session_id": (row.get("Session ID") or "").strip(),
                "quiz_link_id": (row.get("Quiz Link ID") or "").strip(),
                "pki_score": row.get("PKI Score"),
                "created_at": (row.get("Created At") or "").strip(),
                "perceptions": perceptions,
            }
            if norm["result_id"]:
                rows.append(norm)
    return rows


def build_context_for_result(result: Dict[str, Any], quiz_meta: Dict[str, QuestionMeta]) -> Dict[str, Any]:
    """Construct a compact, model-friendly context for a single result.

    Includes per-question comparisons and associated sources.
    """
    items: List[Dict[str, Any]] = []
    all_sources: Dict[str, Source] = {}

    for entry in result.get("perceptions", []):
        qid = str(entry.get("questionId") or "").strip()
        meta = quiz_meta.get(qid)
        question_text = str(entry.get("questionText") or (meta.question if meta else "")).strip()
        user_val = entry.get("userGuessValue")
        actual_val = entry.get("actualValue")
        category = meta.category if meta else None
        # Derive a simple error metric when numeric
        err = None
        try:
            if user_val is not None and actual_val is not None:
                err = abs(float(user_val) - float(actual_val))
        except Exception:
            err = None

        srcs: List[Dict[str, str]] = []
        if meta and meta.sources:
            for s in meta.sources:
                srcs.append({"name": s.name or s.url, "url": s.url})
                all_sources[s.url] = s

        items.append(
            {
                "question_id": qid,
                "question": question_text,
                "category": category,
                "user_guess": user_val,
                "actual_value": actual_val,
                "abs_error": err,
                "sources": srcs,
            }
        )

    # Aggregate simple stats
    errors = [i["abs_error"] for i in items if isinstance(i.get("abs_error"), (int, float))]
    summary = {
        "num_questions": len(items),
        "mean_abs_error": (sum(errors) / len(errors)) if errors else None,
        "num_with_sources": sum(1 for i in items if i.get("sources")),
    }

    return {
        "result_id": result.get("result_id"),
        "session_id": result.get("session_id"),
        "quiz_link_id": result.get("quiz_link_id"),
        "created_at": result.get("created_at"),
        "summary": summary,
        "items": items,
        "candidate_sources": [
            {"name": (s.name or s.url), "url": s.url} for s in all_sources.values()
        ],
    }


def build_prompt_messages(context: Dict[str, Any]) -> List[Dict[str, str]]:
    """Create chat messages for the Perplexity API."""
    sys_prompt = (
        "You are an educational explainer and fact-grounded analyst. "
        "Given a user's quiz results that compare their guesses against actual values, "
        "identify key misconceptions, quantify where possible, and offer concise, actionable guidance to learn. "
        "Cite sources with direct URLs. Prefer the provided candidate sources when relevant. "
        "Be neutral, clear, and avoid fluff."
    )

    # Put a compact JSON in the user message to keep it deterministic
    user_payload = {
        "task": "Analyze quiz performance and provide educational guidance with citations.",
        "result_context": context,
        "requirements": [
            "Summarize overall performance and common patterns of error/bias.",
            "For each theme/misconception, explain briefly and suggest how to recalibrate.",
            "Include 4-8 relevant sources with URLs; prefer candidate_sources when suitable.",
            "Return a short bullet list of next steps for learning.",
        ],
        "format": {
            "style": "markdown",
            "sections": [
                "Overview",
                "Key Misconceptions",
                "Recommended Sources",
                "Next Steps",
            ],
        },
    }

    return [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def call_perplexity(messages: List[Dict[str, str]], model: str) -> Dict[str, Any]:
    """Call Perplexity via the official SDK.

    Expects `perplexity` package installed and API key set.
    """
    api_key = os.getenv("PPLX_API_KEY") or os.getenv("PERPLEXITY_API_KEY")
    if not api_key:
        raise RuntimeError("Missing API key. Set PPLX_API_KEY or PERPLEXITY_API_KEY to call Perplexity.")

    # Local import to avoid hard dependency for dry-run users
    from perplexity import Perplexity  # type: ignore

    client = Perplexity(api_key=api_key)
    # The SDK mirrors OpenAI-compatible chat.completions interface
    response = client.chat.completions.create(model=model, messages=messages)

    # Normalize to dict for consistent downstream handling
    if hasattr(response, "model_dump"):
        return response.model_dump()
    if isinstance(response, dict):
        return response
    # Fallback: best-effort conversion
    try:
        import json as _json
        return _json.loads(_json.dumps(response, default=lambda o: getattr(o, "__dict__", str(o))))
    except Exception:
        return {"raw": str(response)}


def extract_markdown_content(response_json: Dict[str, Any]) -> Optional[str]:
    """Best-effort extraction of assistant content from Perplexity-like responses."""
    try:
        choices = response_json.get("choices") or []
        if not choices:
            return None
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content
    except Exception:
        pass
    return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze quiz results via Perplexity API")
    parser.add_argument("--quizzes", type=Path, default=Path("quizzes.csv"))
    parser.add_argument("--results", type=Path, default=Path("results.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("analyses"))
    parser.add_argument("--model", type=str, default="sonar-pro")
    parser.add_argument("--limit", type=int, default=0, help="Max number of results to process (0=all)")
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to sleep between API calls")
    parser.add_argument("--dry-run", action="store_true", help="Do not call API, only write prompts")
    parser.add_argument("--print-json", action="store_true", help="Print raw API response JSON to stdout")

    args = parser.parse_args(argv)

    # Lightweight .env support (no extra dependency required)
    def load_env_file(env_path: Path) -> None:
        if not env_path.exists():
            return
        try:
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.lower().startswith("export "):
                    line = line[7:].lstrip()
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                # Do not override pre-existing env vars
                if key and (key not in os.environ):
                    os.environ[key] = val
        except Exception:
            # Non-fatal if .env is malformed
            pass

    # Load .env in current working directory if present
    load_env_file(Path(".env"))

    if not args.quizzes.exists():
        print(f"Quizzes file not found: {args.quizzes}", file=sys.stderr)
        return 1
    if not args.results.exists():
        print(f"Results file not found: {args.results}", file=sys.stderr)
        return 1

    args.output_dir.mkdir(parents=True, exist_ok=True)

    quizzes = load_quizzes(args.quizzes)
    results = load_results(args.results)

    processed = 0
    for res in results:
        if args.limit and processed >= args.limit:
            break
        quiz_link_id = res.get("quiz_link_id")
        qmeta = quizzes.get(quiz_link_id) or {}
        context = build_context_for_result(res, qmeta)
        messages = build_prompt_messages(context)

        out_dir = args.output_dir / str(res.get("result_id") or f"session_{res.get('session_id')}")
        out_dir.mkdir(parents=True, exist_ok=True)

        prompt_path = out_dir / "prompt.json"
        with prompt_path.open("w", encoding="utf-8") as f:
            json.dump({"model": args.model, "messages": messages}, f, ensure_ascii=False, indent=2)

        do_call = not args.dry_run and (os.getenv("PPLX_API_KEY") or os.getenv("PERPLEXITY_API_KEY"))
        response_json: Optional[Dict[str, Any]] = None
        if do_call:
            try:
                response_json = call_perplexity(messages, args.model)
            except Exception as e:
                # Write error file and continue
                err_path = out_dir / "error.txt"
                err_path.write_text(str(e), encoding="utf-8")
        else:
            # Indicate skipped call
            (out_dir / "skipped_api_call.txt").write_text(
                "API call skipped (no key or dry-run). Set PPLX_API_KEY to enable.",
                encoding="utf-8",
            )

        if response_json:
            (out_dir / "response.json").write_text(
                json.dumps(response_json, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            md = extract_markdown_content(response_json)
            if md:
                (out_dir / "analysis.md").write_text(md, encoding="utf-8")

            # Also print to stdout to show the API response "in the chat"
            print("\n=== Perplexity Response (Result ID: {} ) ===".format(res.get("result_id")))
            if md:
                print(md)
            else:
                print("(No assistant content found in response)")
            if args.print_json:
                print("\n--- Raw API Response JSON ---")
                print(json.dumps(response_json, ensure_ascii=False, indent=2))

        processed += 1
        if args.sleep and do_call:
            time.sleep(args.sleep)

    print(f"Processed {processed} result(s). Output in: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
