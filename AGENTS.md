# Repository Guidelines

## Project Structure & Module Organization
- `app.py` runs the Flask app with a local SQLite database (`outreach.db`) and renders `templates/index.html`.
- `api/` contains Vercel serverless handlers that use Supabase; `api/cron/` holds scheduled jobs. Routing and cron schedules live in `vercel.json`.
- `public/index.html` is a static copy of the UI for Vercel hosting; keep UI edits aligned with `templates/index.html`.
- `supabase-setup.sql` defines Supabase schema; `requirements.txt` lists serverless/cron dependencies; `run.sh` bootstraps local dev.

## Build, Test, and Development Commands
- `./run.sh` creates/activates `venv`, installs Flask, and starts `python app.py` for local SQLite development.
- `python app.py` runs the Flask server directly if your environment is already set up.
- `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt` installs Supabase/Resend libs for API/cron work.

## Coding Style & Naming Conventions
- Python uses 4-space indentation and snake_case; keep one endpoint per file in `api/`.
- HTML/CSS follows existing class names and layout patterns; if you change UI, update both `templates/index.html` and `public/index.html`.
- Config uses uppercase env vars like `SUPABASE_URL`, `SUPABASE_KEY`, `RESEND_API_KEY`, and `CRON_SECRET`.

## Testing Guidelines
- No automated test suite is configured. Run manual smoke checks:
- `curl http://localhost:5000/api/prospects`
- Open `http://localhost:5000/` and create/update a prospect.

## Commit & Pull Request Guidelines
- Commit subjects are imperative, sentence case (e.g., "Add tasks table and API endpoints").
- PRs should include a concise summary, manual test notes, and UI screenshots when editing `templates/index.html` or `public/index.html`.
- Link related work items (e.g., from `IDEAS.md`) when applicable.

## Security & Configuration Tips
- Do not commit secrets; use environment variables for Supabase and Resend credentials.
- When adding cron endpoints, update `vercel.json` and document new env vars or schedules.
