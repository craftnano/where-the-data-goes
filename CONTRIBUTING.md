# Contributing

Thanks for looking. This is a data-art piece first and a maintained dataset second — but the data gets better the more eyes are on it. A few ways to help:

## Fixing a company's sub-processor list

If you spot a company whose list looks wrong (missing entries, stale data, misclassified disclosure class), either:

1. **Open an issue** with the company name, the source URL you'd use, and what the current graph gets wrong. Fastest path.
2. **Open a pull request** with either:
   - A manual observation in `data/manual/<company>-subprocessors-manual.json` (schema: see `data/manual/avante-ai-subprocessors-manual.json` for the canonical shape, or `data/manual/microsoft-online-services-subprocessors-manual.json` for a PDF-sourced example). Set `paraphrased: false` only if the purpose text is verbatim from the source.
   - An alias addition in `data/aliases.yaml` if the fix is name-collapse (e.g. "X Inc." and "X" are the same company and should share a node).

## Adding a company as a seed

Pick the seed set that fits (`data/other_seeds.csv` is the catch-all for one-offs; create a new CSV + `.meta.json` for a new themed set like `fortune_500_seeds.csv`). Then run:

```bash
.venv/bin/python agent.py --company "<Name>" --website "<URL>" \
  --hint "<optional context, especially trust-center URL if known>"
```

Open a PR with the new seed row and the resulting transcript under `data/transcripts/<run_id>/`.

## Reporting a wrong edge

Edges with `source: "live"` or `source: "archive"` reflect what the agent extracted from the company's own page at the dated URL. If the edge is wrong because the agent misread the page, open an issue — include the entry row from the transcript JSON so I can trace what went wrong. Edges with `source: "manual"` and `purpose_paraphrased: true` are human-extracted and can be corrected directly in the manual JSON.

## Methodology clarifications

The quick frame:

- **This is a map of data egress between organizations, not of software.** Anything self-hosted — Postgres, Redis, Kafka, DuckDB, Grafana, Elasticsearch, Nginx — is invisible here, because no personal data crosses a corporate boundary.
- **Sub-processor disclosure is a GDPR Article 28 obligation.** B2B SaaS vendors publish named lists; B2C companies disclose "categories of recipients" instead (per Article 13/14). The dataset over-represents B2B SaaS by regulatory design.
- **Edges mean "disclosed," not "verified."** Inclusion does not imply any security, compliance, or data-handling judgment about the company.

## Running the pipeline

See the main [README](README.md). Short version: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/playwright install chromium && cp .env.example .env`. Fill in your Anthropic and Brave Search keys.

## License

Code: MIT (see `LICENSE`).
Data: CC-BY-SA 4.0 (see `LICENSE-DATA`). PRs that add observations or seed data are agreed to be contributed under the same CC-BY-SA 4.0 unless you explicitly note otherwise in the PR.
