"""One model call per company to find and extract its public sub-processor list.

Phase 1: single company at a time (or a seeds file). Haiku first; Sonnet retry
if Haiku misses. Full transcript logged. Resumable within a run via a JSON
state file. Hard spend cap enforced across the whole run.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import io
import json
import re
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
import httpx
import pdfplumber
from playwright.async_api import async_playwright

import config

SUBP_RE = re.compile(r"sub[\s\-]?processor", re.IGNORECASE)

SYSTEM_PROMPT = """\
You are investigating one company at a time to find its public sub-processor list.

A sub-processor list names the third parties the company relies on to deliver its service (hosting, support, analytics, communications, payments, AI inference, security). It is typically a page on the company's own domain titled "Sub-processors", "List of Sub-processors", "DPA Appendix", etc., or a PDF linked from such a page. It may also be embedded inside a public Data Processing Addendum.

Tools:
- search(query): Brave Search, up to 8 results.
- open(url): fetch a page in a real browser; returns visible text (capped at 25000 chars, preserving the region around any "sub-processor" mention) and outgoing links. If the live page is blocked, open() transparently falls back to the Wayback Machine's most recent capture and the response will include "source":"archive" and "capture_at":"YYYY-MM-DD".
- read_pdf(url): fetch a PDF; returns extracted text (same cap and truncation rule).
- report(...): call this exactly once to finish the task.

Approach:
1. Search for the list, preferring results on the company's own domain.
2. Open the best candidate. If names are on the page, extract them.
3. If the page links to a PDF of the list, read it.
4. If the content is behind a login, NDA, or trust portal (SafeBase, Vanta Trust Center, Whistic, Conveyor, etc.), classify as `trust_portal`.
5. If the company only describes categories of recipients (e.g. "cloud hosting providers", "payment processors") with no actual names, classify as `categories_only`.
6. If pages consistently 4xx/5xx or an anti-bot blocks the browser, classify as `blocked`.
7. If no sub-processor list exists after a real attempt, classify as `nothing_found`.

Archive-sourced lists (important):
When open() returns "source":"archive", it means the LIVE page was blocked and open() fell back to Wayback. In that case:
- disclosure_class stays `blocked` (that's the live classification),
- set source="archive",
- set observation_date to the capture_at value the tool returned (YYYY-MM-DD),
- set source_url to the archive URL,
- still include the entries you extracted from the archive page.
Never claim an archive extraction as a live `named_list`.

Rules:
- `purpose` and `location` must be verbatim from the source. Do not paraphrase, translate, or normalize. If the source does not state a purpose or location for an entry, pass an empty string.
- Only include third-party sub-processors. Skip the company's own legal entities and affiliates.
- Call `report` as your final action. You have a budget of 8 tool calls total.
"""

TOOLS = [
    {
        "name": "search",
        "description": "Brave web search. Returns up to 8 results with title, url, and snippet.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "open",
        "description": (
            "Fetch a URL in a headless browser. Returns visible text and outgoing links. "
            "Text is capped at 25000 chars; if truncated, the region around 'sub-processor' "
            "mentions is preserved."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "read_pdf",
        "description": (
            "Fetch and extract text from a PDF URL. Same 25000-char cap and truncation rule as open()."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "report",
        "description": "Return your final answer and end the task. Call exactly once.",
        "input_schema": {
            "type": "object",
            "properties": {
                "disclosure_class": {
                    "type": "string",
                    "enum": ["named_list", "categories_only", "trust_portal", "blocked", "nothing_found"],
                },
                "source_url": {
                    "type": ["string", "null"],
                    "description": "URL of the page or PDF where the list was found; null if no list was found.",
                },
                "source": {
                    "type": "string",
                    "enum": ["live", "archive"],
                    "description": "'live' if the list came from the live page; 'archive' if open() fell back to Wayback.",
                },
                "observation_date": {
                    "type": ["string", "null"],
                    "description": "YYYY-MM-DD. For archive-sourced lists, the capture_at date returned by open(). Null for live.",
                },
                "product_scope": {
                    "type": ["string", "null"],
                    "description": "If this list is scoped to a specific product line rather than the whole company, name the scope here (e.g. 'Meta for Work / Horizon Managed Solutions'). Null for company-wide lists.",
                },
                "entries": {
                    "type": "array",
                    "description": "Sub-processors named on the list. Empty when disclosure_class is not 'named_list' and no archive-sourced entries were found.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "purpose": {"type": "string", "description": "Verbatim purpose text from the source, or empty string."},
                            "location": {"type": "string", "description": "Verbatim location text from the source, or empty string."},
                        },
                        "required": ["name", "purpose", "location"],
                    },
                },
            },
            "required": ["disclosure_class", "source_url", "entries"],
        },
    },
]

REPORT_ONLY_TOOLS = [t for t in TOOLS if t["name"] == "report"]


def truncate_text(text: str, cap: int = config.MAX_TEXT_CHARS) -> str:
    if len(text) <= cap:
        return text
    matches = list(SUBP_RE.finditer(text))
    if not matches:
        return text[:cap] + "\n\n[... truncated to 25000 chars; no 'sub-processor' mention in full text ...]"
    first = matches[0].start()
    start = max(0, first - 2000)
    end = min(len(text), start + cap)
    start = max(0, end - cap)
    prefix = "[... earlier content truncated ...]\n\n" if start > 0 else ""
    suffix = "\n\n[... later content truncated ...]" if end < len(text) else ""
    return prefix + text[start:end] + suffix


def block_to_dict(block: Any) -> dict:
    # The SDK's own serializer handles every block type (text, tool_use,
    # thinking, redacted_thinking) and preserves fields like signature that
    # the API requires on the next turn.
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)
    return dict(block)


def compute_cost(model: str, usage: dict) -> float:
    pricing = config.PRICING.get(model, {"input": 0.0, "output": 0.0})
    return (
        usage.get("input_tokens", 0) * pricing["input"] / 1_000_000
        + usage.get("output_tokens", 0) * pricing["output"] / 1_000_000
    )


class RateLimiter:
    """Simple serializing limiter: at most one request per `interval` seconds."""

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self.lock = asyncio.Lock()
        self.last = 0.0

    async def wait(self) -> None:
        async with self.lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            delay = self.last + self.interval - now
            if delay > 0:
                await asyncio.sleep(delay)
            self.last = loop.time()


async def tool_search(query: str, http: httpx.AsyncClient, limiter: RateLimiter) -> str:
    await limiter.wait()
    r = await http.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={"X-Subscription-Token": config.BRAVE_API_KEY, "Accept": "application/json"},
        params={"q": query, "count": config.SEARCH_RESULTS, "safesearch": "off"},
    )
    if r.status_code >= 400:
        return json.dumps({"error": f"HTTP {r.status_code}", "body": r.text[:500]})
    data = r.json()
    results = []
    for item in (data.get("web", {}).get("results", []) or [])[: config.SEARCH_RESULTS]:
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("description", ""),
            }
        )
    return json.dumps(results, indent=2)


WAYBACK_AVAILABLE_API = "https://archive.org/wayback/available"


async def wayback_closest(url: str, http: httpx.AsyncClient) -> tuple[str | None, str | None]:
    """Return (archive_url, YYYY-MM-DD capture date) for the most recent capture, or (None, None)."""
    try:
        r = await http.get(WAYBACK_AVAILABLE_API, params={"url": url}, timeout=15.0)
    except Exception:
        return None, None
    if r.status_code >= 400:
        return None, None
    try:
        data = r.json()
    except Exception:
        return None, None
    closest = (data.get("archived_snapshots") or {}).get("closest") or {}
    if not closest.get("available"):
        return None, None
    archive_url = closest.get("url")
    ts = closest.get("timestamp") or ""
    capture_date = f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}" if len(ts) >= 8 else None
    return archive_url, capture_date


def _write_snapshot_meta(path: Path, meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2))


async def _goto(page, url: str) -> int:
    try:
        resp = await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        return resp.status if resp else 0
    except Exception:
        return 0


async def tool_open(url: str, context, http: httpx.AsyncClient, run_id: str) -> str:
    page = await context.new_page()
    source = "live"
    final_url = url
    capture_date: str | None = None
    status = 0
    try:
        status = await _goto(page, url)
        if status == 0 or status >= 400:
            # Live blocked or failed; try Wayback.
            archive_url, capture_date = await wayback_closest(url, http)
            if archive_url:
                archive_status = await _goto(page, archive_url)
                if 0 < archive_status < 400:
                    source = "archive"
                    final_url = archive_url
                    status = archive_status
                else:
                    return json.dumps(
                        {"error": f"live HTTP {status}; wayback HTTP {archive_status}", "url": url},
                    )
            else:
                return json.dumps(
                    {"error": f"live HTTP {status}; no wayback capture", "url": url},
                )
        await page.wait_for_timeout(1500)
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        # JS-heavy pages (trust centers, SafeBase, Vanta, Conveyor shells) often
        # render their list after domcontentloaded. If the first read looks empty,
        # give the SPA more time and nudge any lazy content by scrolling.
        if len(text or "") < 400:
            try:
                await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                pass
            await page.wait_for_timeout(6000)
            text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        links = await page.evaluate(
            "() => Array.from(document.querySelectorAll('a[href]')).map(a => a.href)"
        )
        html = await page.content()
    finally:
        await page.close()

    content_hash = hashlib.sha256(html.encode("utf-8")).hexdigest()[:16]
    snap_dir = config.SNAPSHOTS_DIR / run_id
    (snap_dir / f"{content_hash}.html").parent.mkdir(parents=True, exist_ok=True)
    (snap_dir / f"{content_hash}.html").write_text(html, encoding="utf-8")
    _write_snapshot_meta(
        snap_dir / f"{content_hash}.meta.json",
        {
            "hash": content_hash,
            "url": final_url,
            "live_url": url if source == "archive" else None,
            "source": source,
            "capture_at": capture_date,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
        },
    )

    seen = set()
    unique_links: list[str] = []
    for link in links:
        if link and link not in seen:
            seen.add(link)
            unique_links.append(link)
        if len(unique_links) >= 100:
            break

    out: dict = {
        "url": final_url,
        "source": source,
        "status": status,
        "text": truncate_text(text or ""),
        "links": unique_links,
    }
    if source == "archive":
        out["live_url"] = url
        out["capture_at"] = capture_date
    return json.dumps(out, indent=2)


async def tool_read_pdf(url: str, http: httpx.AsyncClient, run_id: str) -> str:
    try:
        r = await http.get(
            url,
            headers={"From": config.CONTACT_EMAIL, "User-Agent": config.USER_AGENT},
            follow_redirects=True,
        )
    except Exception as e:
        return json.dumps({"error": f"fetch failed: {type(e).__name__}: {e}", "url": url})
    if r.status_code >= 400:
        return json.dumps({"error": f"HTTP {r.status_code}", "url": url})

    content_hash = hashlib.sha256(r.content).hexdigest()[:16]
    snap_dir = config.SNAPSHOTS_DIR / run_id
    (snap_dir / f"{content_hash}.pdf").parent.mkdir(parents=True, exist_ok=True)
    (snap_dir / f"{content_hash}.pdf").write_bytes(r.content)
    _write_snapshot_meta(
        snap_dir / f"{content_hash}.meta.json",
        {
            "hash": content_hash,
            "url": url,
            "live_url": None,
            "source": "live",
            "capture_at": None,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "status": r.status_code,
        },
    )

    try:
        chunks = []
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            for page in pdf.pages:
                chunks.append(page.extract_text() or "")
        text = "\n".join(chunks)
    except Exception as e:
        return json.dumps({"error": f"pdf parse failed: {type(e).__name__}: {e}", "url": url})

    return json.dumps({"url": url, "source": "live", "text": truncate_text(text)}, indent=2)


async def dispatch_tool(
    name: str,
    tool_input: dict,
    context,
    http: httpx.AsyncClient,
    brave: RateLimiter,
    run_id: str,
) -> str:
    try:
        if name == "search":
            return await tool_search(tool_input["query"], http, brave)
        if name == "open":
            return await tool_open(tool_input["url"], context, http, run_id)
        if name == "read_pdf":
            return await tool_read_pdf(tool_input["url"], http, run_id)
        return json.dumps({"error": f"unknown tool: {name}"})
    except Exception as e:
        return json.dumps({"error": f"tool crashed: {type(e).__name__}: {e}"})


async def run_agent_loop(
    client: anthropic.AsyncAnthropic,
    context,
    http: httpx.AsyncClient,
    brave: RateLimiter,
    model: str,
    company: str,
    website: str,
    run_id: str,
    hint: str | None = None,
) -> dict:
    user_prompt = (
        f"Find the public sub-processor list for: {company} ({website}).\n\n"
        "Investigate using the tools, then call `report` with your final answer."
    )
    if hint:
        user_prompt += f"\n\nHint: {hint}"
    messages: list[dict] = [{"role": "user", "content": user_prompt}]
    transcript_turns: list[dict] = [{"role": "user", "content": user_prompt}]

    tool_count = 0
    final_report: dict | None = None
    stop_reason = None
    usage_total = {"input_tokens": 0, "output_tokens": 0}

    while tool_count < config.MAX_TOOL_CALLS:
        exposed = REPORT_ONLY_TOOLS if tool_count >= config.MAX_TOOL_CALLS - 1 else TOOLS
        response = await client.messages.create(
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=exposed,
            messages=messages,
        )
        usage_total["input_tokens"] += response.usage.input_tokens
        usage_total["output_tokens"] += response.usage.output_tokens
        stop_reason = response.stop_reason

        assistant_content = [block_to_dict(b) for b in response.content]
        messages.append({"role": "assistant", "content": assistant_content})
        transcript_turns.append({"role": "assistant", "content": assistant_content, "stop_reason": stop_reason})

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            break

        tool_results = []
        for tu in tool_uses:
            tool_count += 1
            if tu.name == "report":
                final_report = tu.input
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": tu.id, "content": "report received; task complete."}
                )
            else:
                result_text = await dispatch_tool(tu.name, tu.input, context, http, brave, run_id)
                tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": result_text})

        messages.append({"role": "user", "content": tool_results})
        transcript_turns.append({"role": "user", "content": tool_results})

        if final_report is not None:
            break

    # If the budget ran out without a report, force one last call that must call `report`.
    if final_report is None:
        force_msg = (
            "You have reached the end of your tool budget. Based on everything you have found so far, "
            "call `report` now with your best answer. If you did not find a sub-processor list, "
            "classify as `nothing_found` with source_url=null and entries=[]."
        )
        messages.append({"role": "user", "content": force_msg})
        transcript_turns.append({"role": "user", "content": force_msg, "forced": True})
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=REPORT_ONLY_TOOLS,
                tool_choice={"type": "tool", "name": "report"},
                messages=messages,
            )
            usage_total["input_tokens"] += response.usage.input_tokens
            usage_total["output_tokens"] += response.usage.output_tokens
            stop_reason = response.stop_reason
            assistant_content = [block_to_dict(b) for b in response.content]
            transcript_turns.append(
                {"role": "assistant", "content": assistant_content, "stop_reason": stop_reason, "forced": True}
            )
            for b in response.content:
                if b.type == "tool_use" and b.name == "report":
                    final_report = b.input
                    break
        except Exception as e:
            transcript_turns.append({"role": "system", "error": f"forced report failed: {type(e).__name__}: {e}"})

    # Last-ditch synthesis so downstream code always has a report.
    if final_report is None:
        final_report = {
            "disclosure_class": "nothing_found",
            "source_url": None,
            "source": "live",
            "observation_date": None,
            "entries": [],
            "_synthesized": "model never produced a report; defaulted to nothing_found",
        }

    return {
        "model": model,
        "stop_reason": stop_reason,
        "tool_calls_used": tool_count,
        "usage": usage_total,
        "cost_usd": compute_cost(model, usage_total),
        "report": final_report,
        "turns": transcript_turns,
    }


def is_miss(report: dict | None) -> bool:
    if report is None:
        return True
    cls = report.get("disclosure_class")
    if cls == "nothing_found":
        return True
    entries = report.get("entries") or []
    if cls == "named_list" and len(entries) < 3:
        return True
    return False


def company_slug(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()


class SpendLedger:
    """Cumulative USD spent across every run in the project. Enforces the Phase 1 ceiling.

    First-time init backfills by reading every existing data/runs/*/state.json, so the
    ledger starts from true cumulative-to-date instead of zero.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = asyncio.Lock()
        if path.exists():
            self.data = json.loads(path.read_text())
        else:
            total = 0.0
            seen_runs = []
            if config.RUNS_DIR.exists():
                for sp in config.RUNS_DIR.glob("*/state.json"):
                    s = json.loads(sp.read_text())
                    cost = float(s.get("total_cost_usd") or 0.0)
                    total += cost
                    seen_runs.append({"run_id": s.get("run_id"), "cost_usd": cost, "started_at": s.get("started_at")})
            self.data = {
                "ceiling_usd": config.PHASE1_SPEND_CEILING_USD,
                "total_usd": round(total, 6),
                "runs": seen_runs,
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.data, indent=2))

    @property
    def total(self) -> float:
        return float(self.data.get("total_usd", 0.0))

    def ceiling_reached(self) -> bool:
        return self.total >= config.PHASE1_SPEND_CEILING_USD

    async def add(self, delta: float, run_id: str) -> float:
        async with self.lock:
            self.data["total_usd"] = round(self.data.get("total_usd", 0.0) + delta, 6)
            # Keep a lightweight per-run tally so the ledger stays debuggable.
            runs = self.data.setdefault("runs", [])
            for row in runs:
                if row.get("run_id") == run_id:
                    row["cost_usd"] = round(row.get("cost_usd", 0.0) + delta, 6)
                    break
            else:
                runs.append({"run_id": run_id, "cost_usd": round(delta, 6)})
            self.path.write_text(json.dumps(self.data, indent=2))
            return self.data["total_usd"]


class RunState:
    """Resumable per-run state. Writes to data/runs/<run_id>/state.json on every mutation."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.path = config.RUNS_DIR / run_id / "state.json"
        self.lock = asyncio.Lock()
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
            # Keep backwards-compatible with earlier runs that lacked failures.
            self.data.setdefault("failures", {})
        else:
            self.data = {
                "run_id": run_id,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "kind": "live",
                "primary_model": config.MODEL_HAIKU,
                "retry_model": config.MODEL_SONNET,
                "completed_companies": [],
                "failures": {},
                "total_cost_usd": 0.0,
                "aborted": False,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._persist()

    def _persist(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2))

    async def add_cost(self, delta: float) -> float:
        async with self.lock:
            self.data["total_cost_usd"] = round(self.data["total_cost_usd"] + delta, 6)
            self._persist()
            return self.data["total_cost_usd"]

    async def mark_complete(self, company: str) -> None:
        async with self.lock:
            if company not in self.data["completed_companies"]:
                self.data["completed_companies"].append(company)
            self._persist()

    async def record_failure(self, company: str, msg: str) -> None:
        async with self.lock:
            self.data["failures"][company] = msg
            self._persist()

    async def mark_aborted(self) -> None:
        async with self.lock:
            self.data["aborted"] = True
            self._persist()

    def is_done(self, company: str) -> bool:
        return company in self.data["completed_companies"]

    @property
    def cost_total(self) -> float:
        return self.data["total_cost_usd"]


def save_transcript(run_id: str, company: str, website: str, attempts: list[dict], final: dict) -> Path:
    path = config.TRANSCRIPTS_DIR / run_id / f"{company_slug(company)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "company": company,
        "website": website,
        "run_id": run_id,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "attempts": attempts,
        "final": final,
    }
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


async def crawl_company(
    client: anthropic.AsyncAnthropic,
    browser,
    http: httpx.AsyncClient,
    brave: RateLimiter,
    company: str,
    website: str,
    state: RunState,
    ledger: SpendLedger,
    hint: str | None = None,
) -> None:
    if state.is_done(company):
        print(f"[skip] {company}", flush=True)
        return
    if ledger.ceiling_reached():
        await state.mark_aborted()
        print(f"[abort-phase1] cumulative spend ${ledger.total:.4f} >= ceiling ${config.PHASE1_SPEND_CEILING_USD:.2f}; skipping {company}", flush=True)
        return
    if state.cost_total >= config.HARD_SPEND_CAP_USD:
        await state.mark_aborted()
        print(f"[abort] per-run cap reached, skipping {company}", flush=True)
        return

    # Each company gets its own browser context so cookies and storage don't bleed.
    context = await browser.new_context(
        user_agent=config.USER_AGENT,
        extra_http_headers={"From": config.CONTACT_EMAIL},
    )
    attempts: list[dict] = []
    final_choice: dict | None = None
    try:
        for model in config.LIVE_MODEL_SEQUENCE:
            print(f"[run] {company} — {model}", flush=True)
            attempt = await run_agent_loop(client, context, http, brave, model, company, website, state.run_id, hint=hint)
            attempts.append(attempt)
            total = await state.add_cost(attempt["cost_usd"])
            cum = await ledger.add(attempt["cost_usd"], state.run_id)

            if not is_miss(attempt["report"]):
                final_choice = attempt
                break
            if total >= config.HARD_SPEND_CAP_USD:
                print(f"[abort] per-run cap reached mid-company after {model} on {company}", flush=True)
                break
            if cum >= config.PHASE1_SPEND_CEILING_USD:
                print(f"[abort-phase1] cumulative ceiling reached mid-company after {model} on {company}", flush=True)
                break
    finally:
        await context.close()

    if final_choice is None:
        final_choice = max(
            attempts, key=lambda a: len((a["report"] or {}).get("entries") or [])
        ) if attempts else {"report": None, "model": None, "turns": [], "usage": {}, "cost_usd": 0.0, "tool_calls_used": 0, "stop_reason": None}

    transcript_path = save_transcript(state.run_id, company, website, attempts, final_choice)
    await state.mark_complete(company)
    rep = final_choice.get("report") or {}
    print(
        f"[done] {company} — class={rep.get('disclosure_class')} "
        f"entries={len(rep.get('entries') or [])} tool_calls={final_choice.get('tool_calls_used')} "
        f"run_cost=${state.cost_total:.4f} path={transcript_path.name}",
        flush=True,
    )


async def crawl_company_safe(sem: asyncio.Semaphore, *args, **kwargs) -> None:
    async with sem:
        company = kwargs.get("company") or args[4]
        state = kwargs.get("state") or args[6]
        try:
            await crawl_company(*args, **kwargs)  # type: ignore[arg-type]
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            print(f"[fail] {company} — {msg}", flush=True)
            traceback.print_exc()
            await state.record_failure(company, msg)


def load_seeds(path: Path, offset: int = 0, limit: int | None = None) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with path.open() as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i < offset:
                continue
            rows.append((row["company_name"].strip(), row["website"].strip()))
            if limit and len(rows) >= limit:
                break
    return rows


async def main_async(args: argparse.Namespace) -> None:
    for d in (config.RUNS_DIR, config.TRANSCRIPTS_DIR, config.SNAPSHOTS_DIR):
        d.mkdir(parents=True, exist_ok=True)

    run_id = args.run_id or datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ")
    state = RunState(run_id)
    ledger = SpendLedger(config.SPEND_LEDGER_PATH)
    print(f"[run_id] {run_id}", flush=True)
    print(
        f"[ledger] cumulative so far: ${ledger.total:.4f} / ${config.PHASE1_SPEND_CEILING_USD:.2f} Phase 1 ceiling",
        flush=True,
    )
    if ledger.ceiling_reached():
        raise SystemExit(f"Phase 1 cumulative ceiling reached (${ledger.total:.4f}). Refusing to start new run.")

    if args.seeds:
        targets = load_seeds(Path(args.seeds), offset=args.offset, limit=args.limit)
    elif args.company and args.website:
        targets = [(args.company, args.website)]
    else:
        raise SystemExit("Pass either --company+--website or --seeds")

    print(
        f"[plan] {len(targets)} target(s), concurrent={args.concurrent}, "
        f"per-run cap=${config.HARD_SPEND_CAP_USD:.2f}",
        flush=True,
    )

    brave = RateLimiter(interval=1.0)  # 1 req/sec to the Brave API, shared across workers
    sem = asyncio.Semaphore(args.concurrent)
    client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

    started = time.time()
    async with httpx.AsyncClient(timeout=30.0) as http, async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            tasks = [
                asyncio.create_task(
                    crawl_company_safe(
                        sem, client, browser, http, brave, name, website, state, ledger, args.hint
                    )
                )
                for (name, website) in targets
            ]
            await asyncio.gather(*tasks)
        finally:
            await browser.close()
    elapsed = time.time() - started
    print(
        f"[batch_done] run={run_id} companies={len(targets)} "
        f"cost=${state.cost_total:.4f} elapsed={elapsed:.1f}s "
        f"failures={len(state.data['failures'])} cumulative=${ledger.total:.4f}",
        flush=True,
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Sub-processor list extractor agent (Phase 1).")
    p.add_argument("--company", help="Single-company mode: canonical name.")
    p.add_argument("--website", help="Single-company mode: primary website URL.")
    p.add_argument("--seeds", help="Bulk mode: path to a CSV with company_name,website columns.")
    p.add_argument("--offset", type=int, default=0, help="Skip the first N rows of the seeds file.")
    p.add_argument("--limit", type=int, help="After offset, process only the next N rows.")
    p.add_argument("--concurrent", type=int, default=10, help="Max in-flight companies.")
    p.add_argument("--hint", help="Extra context appended to every per-company user prompt (useful for one-offs).")
    p.add_argument("--run-id", help="Resume an existing run id instead of starting a new one.")
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
