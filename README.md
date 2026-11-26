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

## Superadmin-only Bitcoin mining rewards and automatic payouts
- Public block, mempool, hashrate and mining-pool statistics from mempool.space are network-wide data, not proof of personal mining rewards.
- Personal reward reporting uses a separately operated HTTPS pool adapter at MINING_POOL_API_URL + /api/rewards. It must return integer confirmed_sats (the newly confirmed, not-yet-paid reward batch), pending_sats, blocks_found and a unique stable settlement_id for each batch. Configure MINING_POOL_API_URL and MINING_POOL_API_TOKEN.
- GET /api/mining/rewards reports only pool-account credits. If no verified pool adapter is configured, personal block and reward values remain unknown.
- Automatic payouts are disabled until explicitly enabled. Once enabled, every newly confirmed reward batch (minimum 1 satoshi; no 3 BTC threshold) is submitted automatically by the trusted scheduler. Configure MINING_AUTO_PAYOUT_ENABLED=true, MINING_PAYOUT_ADDRESS, MINING_PAYOUT_GATEWAY_URL, MINING_PAYOUT_GATEWAY_TOKEN and MINING_CRON_TOKEN. MINING_PAYOUT_THRESHOLD_SATS is no longer used.
- The payout gateway must expose POST /api/payouts, enforce the Idempotency-Key header, and return a status of submitted, broadcast or confirmed plus a 64-character hexadecimal txid. A trusted scheduler must call POST /api/mining/rewards/settle promptly and repeatedly with X-MINING-CRON-TOKEN. The pool adapter must report each new confirmed batch once with its own settlement_id; coinbase maturity and pool settlement rules still apply.
- Access to /wallet, all /api/mining/* endpoints and all /api/agent/* endpoints is locked to the authenticated superadmin session; other users and agent clients receive HTTP 403. This is an application access gate, not a substitute for protecting the Render service and secrets.
- This app never stores wallet private keys or signs transactions. The payout gateway must be independently reviewed. Verify wallet ownership and the destination address with the pool provider before enabling automatic payouts. A format check is not proof of address validity or ownership.
