# Market Agent

Automated research and alerting system for Indian equity markets.

## What it does
- Aggregates pre-market cues, FII/DII flows, sector breadth, and news sentiment.
- Sends scheduled morning and closing market briefs.
- Generates real-time alerts on index moves, VIX spikes, and institutional flow shocks.

## Setup
1. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Configure environment variables in `.env` based on `.env.example`.
3. Run the agent:
   ```bash
   python3 -m market_agent.main
   ```

## Scheduled Reports
- **Morning brief**: sent at `08:50 IST`
- **Closing report**: sent at `16:00 IST`
- **Breaking alerts**: during market hours, roughly every 40 minutes

## Project Structure
- `market_agent/` — core modules for data, reporting, alerts, Telegram delivery, and caching
- `scripts/` — helper scripts for running scheduled jobs
- `work/` — runtime outputs: logs, reports, and local database (ignored in git)

## Notes
- `.env`, `cookies.txt`, and runtime artifacts in `work/` are not committed.
- Alert delivery is configured via the Telegram bot integration.
