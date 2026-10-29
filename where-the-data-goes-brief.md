# Where All the Data Is Going

A public data art project mapping the sub-processor food chain across every tech company with a public sub-processor list we can find (likely 1,500 to 4,000).

## What this is, and what it is not

This is a public project, not a business. It will not make money and is not meant to. The goal is reach: a piece that gets shared, covered, and cited, and that establishes my name in AI governance and data privacy as someone who shows rather than tells. Success is press coverage, academic and journalist reuse of the dataset, and the piece spreading on its own. Revenue is explicitly out of scope.

Secondary benefit: the crawler, parser, and dataset built here are the same engine a future terms-of-service change monitoring product would use. Build with that reuse in mind (clean schema, dated edges, source URLs preserved) but do not build the product. No subscriptions, no customer watchlists, no alerts.

## The idea

Many SaaS companies publish a sub-processor list: the third parties they rely on to deliver their service. Read one list and it's a page of vendor names. Read a thousand and a shape appears: the same infrastructure, communications, and AI providers recurring everywhere, and each of those relying on others in turn, several hops deep. Nobody sees more than one hop from where they stand. The graph even has loops in it (OpenAI relies on Microsoft, Microsoft relies on OpenAI).

The piece is a picture of how interconnected our data has become. It is not an exposé and there is no villain. Every company in it chose to publish, and the interdependence it shows is simply what modern software looks like. The viewer should come away thinking "I had no idea it was all this connected," not "I've been wronged." The piece draws what the companies disclosed and lets the shape speak.

The feeling we want: not a schematic. A cross-section of something organic, a root system or a cardiovascular map. A body.

## Deliverables

1. **The dataset**, published openly (CSV and Parquet, plus JSON for the web piece), with a methodology note. This is what researchers and journalists will reuse, and reuse is where a lot of the coverage comes from.
2. **The interactive piece**: a browser-based 3D rendering (three.js / WebGL) of the full graph. Hyperscalers at the center, rings outward by hop count, edge weight by downstream dependency. Two interactions: click any company and everything not on its chain fades so you see one organization's exposure against the ghost of the whole; and a time scrubber replaying the last four years of edges appearing, which is where the AI provider story tells itself.
3. **A print**: a poster-scale still of the same structure, no labels except the center, legend small along the bottom edge like a map. Rendered in Blender via a Python script for gallery-grade lighting and depth. Browser render is acceptable for a first version.
4. **An opening narrative**: one ordinary act (an employee typing a question about her leave into a benefits tool) traced outward hop by hop, then the zoom-out to the full graph. This is the thing people share.

## Scope exclusions

- No geographic or region layer. Not tracking where data physically lands. Country of sub-processor may be captured as a raw field if present but is not rendered.
- No crawling behind logins, NDAs, or trust portals (SafeBase, Vanta Trust Center, etc.). Public URLs only.
- No marketing-site terms, acceptable use policies, SLAs, or general privacy policies. Sub-processor lists only, including when the list is embedded inside a public DPA.
- No alerting, monitoring, or customer-facing features.

## Data model

**companies**
- id, canonical_name, raw_names (array of every string seen), website, is_seed (bool), disclosure_class (named_list, categories_only, trust_portal, blocked, nothing_found)

**edges** (company A discloses company B as a sub-processor)
- id, from_company_id, to_company_id, purpose_text (verbatim), purpose_category (normalized: hosting, support, analytics, communications, payments, ai_inference, security, other, unnamed), location_text (verbatim, not rendered)
- first_seen and last_seen are derived from observations, never set directly

**runs**
- id, started_at, kind (live or archive), model, notes. Every crawl is a run. An archive run is dated by the Wayback capture it was extracted from, not by when we fetched it.

**observations**
- run_id, edge_id, source_url, snapshot_id. One row per edge per run that saw it. This is what makes the time lapse possible: an edge appears at its first observation and withers after its last.

**snapshots**
- id, company_id, url, fetched_at, source (live or archive), archive_capture_at, content_hash, raw_path

**transcripts**
- one JSON file per company per run under data/transcripts/, holding the agent's full tool-call history: what it searched, what it opened, what it decided. This is the provenance for the methodology note.

Keep raw name strings alongside canonical entities so the messiness is recoverable.

## Crawl strategy

**An agent, not a scraper.** The first build was a deterministic crawler: guessed URL paths, a strict HTML-table parser, a ladder of fallbacks. It found 3 lists out of 100 in 90 seconds because the real pages come in many shapes (one table, five tables under headings, a landing page linking to a PDF, an accordion, a trust portal) and a rule-based parser recognizes one. The task has two judgment calls, which search result is the list and how to get the names off whatever page it is, and a person makes both in ten seconds. So the crawler is one model call per company with three tools:

- search(query) via the Brave Search API, returning title, URL, snippet
- open(url) via headless Chromium, returning the page's visible text and its links
- read_pdf(url) returning the PDF's text

System prompt, in substance: find this company's public sub-processor list. Pick the search result most likely to be the list itself on the company's own domain. Open it. If the names are there, extract them. If it links to a PDF, read it. If it's behind a login or NDA, say trust_portal. If the company only describes categories of recipients, say categories_only. If a site blocks you, say blocked. Return strict JSON: {disclosure_class, source_url, entries:[{name, purpose, location}]} with purpose and location verbatim.

Model: Haiku 5.5, capped at 8 tool calls per company. If Haiku returns fewer than 3 entries or nothing_found, retry once on Sonnet 5.5 and keep whichever result has entries. Log every transcript. Ten companies concurrent. Expected cost: well under a cent per company on Haiku.

**Seeds and snowball.** Seeds are the current Nasdaq 100 constituents (as a stand-in for "big tech"; the exact membership is not the headline). Every sub-processor named on any list goes into a queue ranked by how many lists have named it; the agent crawls the most-mentioned uncrawled company next. Phase 1 stops at 100 named lists. Later phases run until the snowball yields fewer than five new names per hundred companies crawled.

**Who publishes, and why it matters.** GDPR does not require public sub-processor lists. Article 28 requires a processor to inform its controllers (its customers) of sub-processor changes and let them object; publishing a page is just the cheapest way to do that at scale, reinforced by enterprise procurement questionnaires. So the dataset is self-selected: B2B SaaS vendors acting as processors, selling to enterprise or EU customers, who chose transparency. B2C companies are controllers and only have to disclose "categories of recipients" in a privacy policy, with no names, so they will mostly be absent, and roughly half the Nasdaq 100 (consumer brands, chip makers, biotech, utilities) will land in categories_only or nothing_found. That is a finding, not a failure. State it plainly in the methodology note.

**Time.** Every result is stamped with its run. After the live crawl, for each named list URL, query the Wayback CDX API for captures, take at most one per quarter going back as far as they exist, and run the same extraction on each capture as an archive run dated by capture. This reconstructs several years of movement for the companies that have been publishing longest. Going forward, a scheduled task re-crawls on the first of each month as a new live run. scripts/diff.py prints added and dropped edges between any two runs.

**Entity resolution** collapses "Amazon Web Services", "AWS", "Amazon Web Services, Inc.", and "Amazon Web Services EMEA SARL" to one node. A hand-maintained alias table for the top names covers most edges; for the fuzzy tail, ask a model "are these two strings the same company" and queue its answers for review. The methodology note states that jurisdictional entity differences are collapsed on purpose.

**Fetching manners.** robots.txt, 2 requests per second per host, a normal browser user agent with a contact address in a custom header, one browser instance kept open for the run. No proxies, no CAPTCHA services. A site that blocks the browser is recorded as blocked and its URL is tried via the Wayback Machine instead.

**Snapshots.** Store the raw HTML or PDF and a content hash for every page the agent opens, hits and misses alike, so any decision can be inspected afterward.

## Visual direction

Current direction: shiny, wet, organic. Glossy tubes ("noodles") carrying the data between glossy nodes, like vessels in a body, on a dark tray. A browser prototype exists (three.js) and loads data/graph.json.

- Radial layout. Center: hyperscalers (AWS, Google Cloud, Azure). Second ring: infrastructure and communications vendors that nearly everyone depends on. Outer rings: everyone else, arranged so companies with similar dependency profiles sit near each other.
- Tube thickness by number of downstream companies flowing through it. Node size by how many lists name a company.
- Color by purpose category, so the picture shows what data is used for, not just who has it.
- Interaction: click a company and pulses travel along its tubes to each destination; click where a pulse lands to follow the data another hop; a trail across the top shows the path walked.
- Time lapse: a date scrubber over runs. Tubes grow in at first observation and wither after their last. A "what changed" panel lists added and dropped sub-processors between any two runs.
- The print has almost no text. Density does the talking. Gallery-grade stills and film may be rendered in Blender from the same geometry; the organ look (subsurface scattering) belongs there, not in the browser.

## Phases

1. **Agent and schema**, run on the Nasdaq 100. Output: the disclosure breakdown, the first lists, a sense of how messy names and purposes are.
2. **Snowball to 100 named lists**, then the archive backfill for those URLs. Load the real graph into the prototype and tune the visual against it.
3. **Snowball until the stopping rule triggers.** Lock the dataset, write the methodology note, send it to a reviewer.
4. **Interactive piece, time lapse, and opening narrative.** Ship the web version. Monthly re-crawl scheduled.
5. **Print render and launch**: dataset release, the piece, a LinkedIn essay, outreach to journalists who cover this.

## Open questions

- Which real company anchors the opening narrative, and does its chain actually run three hops in the data.
- Whether the AI providers show up mostly as direct edges or mostly through Azure, Bedrock and Vertex. The data decides which story the time lapse tells.
- Companies that list only categories ("cloud hosting providers") get an edge to an unnamed node and are rendered visibly, so the picture is honest about where the named chain ends.

## Phase 1 instructions for Claude Code

Read the whole brief first. Start a new repo. An earlier attempt at ~/subprocessor-graph built the deterministic crawler described above; do not read or reuse its code. Copy only two data files from it: data/nasdaq100_seeds.csv and data/aliases.yaml (sixty hand-checked canonical names). Build everything else from this brief.

Build agent.py as specified under Crawl strategy: one model call per company, Haiku 5.5 with the search, open and read_pdf tools, strict JSON out, Sonnet retry on misses, full transcript logged, every result stamped with a run id. Add the runs and observations tables and derive first_seen and last_seen from them. Add scripts/export_json.py writing data/graph.json as {"nodes":[{"id","name","kind"}],"edges":[{"from","to","purpose_category","purpose_text","first_seen","last_seen"}]} for the prototype.

Run it on the Nasdaq 100, ten concurrent. Report: the disclosure breakdown for the Nasdaq 100 with company names under each class; Haiku versus Sonnet hit rates; total API cost; the transcripts for five misses so we can see what the agent decided; and the top 20 most-named sub-processors. Then export graph.json. No visualization work; the prototype exists and loads that file.
