# Literature Radar

Static GitHub Pages literature radar with daily OpenAlex retrieval and optional OpenAI summaries.

## Required repository structure

- `.github/workflows/update.yml`
- `data/papers.json`
- `data/journal_metrics.json`
- `scripts/update_papers.py`
- `index.html`
- `styles.css`
- `app.js`
- `requirements.txt`

## GitHub Pages

Set **Settings → Pages → Source** to **GitHub Actions**.

## Optional AI summaries

Add repository secret `OPENAI_API_KEY` under **Settings → Secrets and variables → Actions**.
Without it, the workflow still runs and extracts methods/findings directly from the OpenAlex abstract.

## Manual test

Run **Actions → Update literature radar and deploy → Run workflow**.
