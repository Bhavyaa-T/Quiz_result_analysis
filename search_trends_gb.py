#!/usr/bin/env python3
"""
Query Perplexity's Search API for trending topics on Google Trends (GB).

Behavior
- Uses the official `perplexity` Python SDK: `from perplexity import Perplexity`.
- Auth via `PPLX_API_KEY` or `PERPLEXITY_API_KEY` environment variables (loads from .env if present).
- Restricts results to Google Trends domain and focuses on UK trends.

Usage
  python search_trends_gb.py \
      --max-results 15 \
      --print-json \
      --out results/trends_gb_search.json \
      --include-rss \
      --realtime

Notes
- The Perplexity Search API returns ranked web results. This script filters to
  `trends.google.com` and queries for UK "trending now" information.
- See API docs: https://docs.perplexity.ai/llms-full.txt (Search section)
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, List
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
import xml.etree.ElementTree as ET


def load_env_file(env_path: Path) -> None:
    """Lightweight .env loader without extra dependencies.

    Does not override variables already present in os.environ.
    """
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
            if key and (key not in os.environ):
                os.environ[key] = val
    except Exception:
        # Non-fatal if .env is malformed
        pass


def call_perplexity_search(query: str, max_results: int) -> Dict[str, Any]:
    """Call Perplexity Search API restricted to Google Trends (GB).

    Returns the raw response as a dict (best-effort normalization).
    """
    api_key = os.getenv("PPLX_API_KEY") or os.getenv("PERPLEXITY_API_KEY")
    if not api_key:
        raise RuntimeError("Missing API key. Set PPLX_API_KEY or PERPLEXITY_API_KEY.")

    # Local import to avoid hard dependency for environments not calling the API
    from perplexity import Perplexity  # type: ignore

    client = Perplexity(api_key=api_key)

    # Prefer explicit domain restriction. The SDK forwards kwargs to the API.
    # According to docs, `search_domain_filter` limits results to specific domains.
    # We also include a site-specific query to target the GB trends page.
    kwargs: Dict[str, Any] = {
        "max_results": max_results,
        "search_domain_filter": ["trends.google.com"],
    }

    search = client.search.create(query=query, **kwargs)

    # Normalize to dict
    if hasattr(search, "model_dump"):
        return search.model_dump()
    if isinstance(search, dict):
        return search
    try:
        return json.loads(json.dumps(search, default=lambda o: getattr(o, "__dict__", str(o))))
    except Exception:
        return {"raw": str(search)}


def fetch_gb_daily_rss() -> Dict[str, Any]:
    """Fetch and parse Google's official GB daily trending searches RSS.

    Endpoint: https://trends.google.com/trends/trendingsearches/daily/rss?geo=GB
    Returns a dict with a simplified list of items: title, approx_traffic, pub_date.
    """
    url = "https://trends.google.com/trends/trendingsearches/daily/rss?geo=GB"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; TrendsFetcher/1.0)"}
    try:
        with urlopen(Request(url, headers=headers), timeout=20) as resp:
            data = resp.read()
    except (URLError, HTTPError) as e:
        return {"error": f"Failed to fetch RSS: {e}"}
    except Exception as e:
        return {"error": f"Unexpected error fetching RSS: {e}"}

    try:
        root = ET.fromstring(data)
        # The feed structure is rss/channel/item
        channel = root.find("channel")
        items: List[Dict[str, Any]] = []
        if channel is not None:
            for it in channel.findall("item"):
                title = (it.findtext("title") or "").strip()
                pub_date = (it.findtext("pubDate") or "").strip()
                approx = (it.findtext("ht:approx_traffic") or "").strip()
                items.append({
                    "title": title,
                    "approx_traffic": approx,
                    "pub_date": pub_date,
                })
        return {"source": url, "items": items}
    except Exception as e:
        return {"error": f"Failed to parse RSS XML: {e}"}


def fetch_gb_realtime(max_items: int = 20) -> Dict[str, Any]:
    """Fetch Google Trends real-time trending stories for GB.

    Unofficial JSON endpoint used by the web UI:
      https://trends.google.com/trends/api/realtimetrends?hl=en-GB&tz=0&cat=all&fi=0&fs=0&geo=GB&ri=300&rs=20&sort=0

    The response is prefixed with ")]}'\n" and then JSON. We strip and parse it.
    Returns simplified items with a best-effort extraction of names.
    """
    import json as _json

    base = "https://trends.google.com/trends/api/realtimetrends"
    params = (
        "hl=en-GB&tz=0&cat=all&fi=0&fs=0&geo=GB&ri=300&rs=50&sort=0"
    )
    url = f"{base}?{params}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; TrendsFetcher/1.0)"}
    try:
        with urlopen(Request(url, headers=headers), timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (URLError, HTTPError) as e:
        return {"error": f"Failed to fetch realtime trends: {e}"}
    except Exception as e:
        return {"error": f"Unexpected error fetching realtime: {e}"}

    # Strip anti-CSRF prefix
    prefix = ")]}'"
    if raw.startswith(prefix):
        raw = raw[len(prefix):].lstrip()  # remove trailing newline/space

    try:
        data = _json.loads(raw)
    except Exception as e:
        return {"error": f"Failed to parse realtime JSON: {e}"}

    items: List[Dict[str, Any]] = []
    try:
        summaries = (
            data.get("storySummaries") or {}
        )
        stories = summaries.get("trendingStories") or []
        for s in stories:
            title = (s.get("title") or "").strip()
            entities = s.get("entityNames") or []
            # Choose a display label: title or first entity
            label = title or (entities[0] if entities else "")
            started = None
            try:
                started = s.get("timeRange") or s.get("timestamp")
            except Exception:
                started = None
            items.append({
                "label": label,
                "entities": entities,
                "started": started,
            })
    except Exception:
        pass

    # Fallback older structure (if present)
    if not items:
        try:
            for t in (data.get("trendingSearches") or []):
                q = t.get("title", {}).get("query") or ""
                items.append({"label": q, "entities": [q] if q else [], "started": t.get("formattedTraffic")})
        except Exception:
            pass

    return {"items": items[:max_items]}


def format_result_line(item: Dict[str, Any]) -> str:
    title = item.get("title") or item.get("name") or "(no title)"
    url = item.get("url") or item.get("link") or ""
    snippet = item.get("snippet") or item.get("description") or ""
    if snippet:
        snippet = snippet.strip().replace("\n", " ")
    return f"- {title} :: {url}\n  {snippet}"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Search Google Trends (GB) via Perplexity Search API")
    parser.add_argument(
        "--max-results",
        type=int,
        default=12,
        help="Maximum number of search results to return",
    )
    parser.add_argument(
        "--query",
        type=str,
        default=(
            "site:trends.google.com Trending now in the United Kingdom "
            "url:trends.google.com/trending?geo=GB"
        ),
        help="Search query text (defaults to UK 'trending now' on Google Trends)",
    )
    parser.add_argument("--out", type=Path, default=None, help="Optional path to write raw JSON results")
    parser.add_argument("--print-json", action="store_true", help="Print raw JSON to stdout")
    parser.add_argument("--include-rss", action="store_true", help="Also fetch GB daily RSS and print top items")
    parser.add_argument("--realtime", action="store_true", help="Also fetch GB realtime trending names from Google")
    parser.add_argument("--search-only", action="store_true", help="Only run Perplexity search (no RSS/realtime)")

    args = parser.parse_args(argv)

    # Load .env if present
    load_env_file(Path(".env"))

    # If user didn't request RSS or realtime explicitly, default to realtime names.
    if not args.include_rss and not args.realtime and not args.search_only:
        args.realtime = True

    try:
        resp = call_perplexity_search(args.query, args.max_results)
    except Exception as e:
        print(f"Error calling Perplexity Search API: {e}")
        resp = {}

    # Extract results array conservatively
    results = []
    try:
        # Some SDKs use an attribute; we normalized to dict
        results = resp.get("results") or resp.get("data") or []
    except Exception:
        results = []

    # Print realtime and/or RSS first for clear names, then show search results
    if args.realtime:
        print("\nGB Realtime Trending (web UI API):")
        rt = fetch_gb_realtime(args.max_results)
        if rt.get("error"):
            print(rt["error"])
            # Automatic fallback to RSS for at least some names
            print("Falling back to GB Daily RSS...")
            rss = fetch_gb_daily_rss()
            if rss.get("error"):
                print(rss["error"])
            else:
                items = rss.get("items") or []
                for i, item in enumerate(items[:args.max_results], start=1):
                    t = item.get("title") or "(no title)"
                    a = item.get("approx_traffic") or ""
                    d = item.get("pub_date") or ""
                    line = f"{i}. {t}"
                    if a:
                        line += f" — {a}"
                    if d:
                        line += f" — {d}"
                    print(line)
        else:
            for i, item in enumerate(rt.get("items", []), start=1):
                label = item.get("label") or "(no label)"
                ents = item.get("entities") or []
                started = item.get("started") or ""
                extra = f" — {', '.join(ents)}" if ents else ""
                if started:
                    extra += f" — {started}"
                print(f"{i}. {label}{extra}")

    if args.include_rss:
        print("\nGB Daily Trending Searches (RSS):")
        rss = fetch_gb_daily_rss()
        if rss.get("error"):
            print(rss["error"])
        else:
            items = rss.get("items") or []
            for i, item in enumerate(items[:args.max_results], start=1):
                t = item.get("title") or "(no title)"
                a = item.get("approx_traffic") or ""
                d = item.get("pub_date") or ""
                line = f"{i}. {t}"
                if a:
                    line += f" — {a}"
                if d:
                    line += f" — {d}"
                print(line)

    if not args.search_only:
        print("\nTop results restricted to trends.google.com (GB):")
        if not results:
            print("(No results returned)")
        else:
            for item in results:
                try:
                    print(format_result_line(item))
                except Exception:
                    # Fallback printing if unexpected structure
                    print(f"- {item}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(resp, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nWrote raw JSON to: {args.out}")

    if args.print_json:
        print("\n--- Raw Search JSON ---")
        print(json.dumps(resp, ensure_ascii=False, indent=2))

    # end main

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
