# AUREALIS OMNIA — Universal Superadmin AI

A new modular superadmin console for AI research, authorized website analysis, API connector registration, custom character personas, invention/IP dossiers and a demo-only asset/idea marketplace.

## Deploy on Render
- Repository: gaussa72-boop/Aurealis-Creative-AI
- Runtime: Python
- Build: pip install -r requirements.txt
- Start: gunicorn --bind 0.0.0.0:$PORT app:app
- Region: Frankfurt

## Required Render environment
- SUPERADMIN_PASSWORD: create a strong unique password in Render; never commit it.
- OPENAI_API_KEY: server-side API key for Responses API and web search.
- FLASK_SECRET_KEY: generate a long random secret.
- OPENAI_MODEL: optional; default gpt-5.6.

## Current capabilities
- Superadmin-only login and dashboard.
- General AI assistant using the OpenAI Responses API with web_search.
- User-created assistant personas; link to the existing Ate Rea CharacterVerse creator for photo/video-based character design.
- API connector registry (metadata only; each API still needs a reviewed adapter, auth scopes and secret configuration).
- Authorized public homepage analysis and a site-clone blueprint (not a full website copier).
- Invention/IP dossiers for later patent-professional review.
- Demo-only asset/idea/IP listings with the requested 70/30 calculation stored as display metadata. No real investments, securities, token issuance, custody, order matching or payouts.

## Important limitations
- This is not a copy of the whole Internet, nor a universal index of every database. Web search is performed through the configured provider; connectors must be added lawfully and individually.
- Website analysis requires ownership or explicit permission. It refuses local/private IP destinations and does not bypass logins, paywalls, CAPTCHAs or technical restrictions.
- SQLite is local/ephemeral on Render. For durable multi-device production use, provision PostgreSQL and configure a persistent DATABASE_PATH or migrate the app to PostgreSQL before storing important records.
- No actual patent application is filed. Patentability and filing must be checked by a qualified patent attorney and the relevant patent office.
- Financial trading and fractional ownership remain disabled until a German/EU legal assessment, permissions, disclosures, custody/payment and market infrastructure requirements are satisfied.

## Security
API keys and superadmin credentials must be configured only in Render Environment. Do not expose them in frontend code or Git.


## Bitcoin mining and ASIC connection
- Public Bitcoin block, mempool, transaction, hashrate and mining-pool data are explorer data from mempool.space. They are not proof that this web service mines Bitcoin or earns rewards for the account owner.
- The wallet exposes a read-only ASIC telemetry bridge at `GET /api/mining/hardware/status`.
- Configure `MINING_GATEWAY_URL` (HTTPS only) and `MINING_GATEWAY_TOKEN` as protected Render environment variables. The gateway must expose authenticated `GET /api/status` JSON with a `connected` field and optional telemetry such as `hashrate`, `power_w`, `temperature_c`, `pool`, accepted/rejected shares and uptime.
- The bridge is read-only: it does not configure pools, start/stop miners, sign transactions, custody keys or request payouts. Those capabilities require a separately reviewed, explicitly approved hardware controller and verified pool account.
- Without both environment variables the endpoint accurately reports `not_configured`; no hardware connection is assumed.
