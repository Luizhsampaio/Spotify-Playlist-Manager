# Working rules

- Implement one requested increment at a time and validate it with synthetic tests.
- Do not add an LLM SDK, provider API, or AI API key.
- Keep `SPOTIFY_AI_POLICY_MODE=blocked`. Do not weaken it without an explicit ADR and policy review.
- Never read real Spotify responses, tokens, playlist names, `.env`, or `.local-data/spotify/`
  into an agent context. Gitignore is not an access-control boundary.
- Validate UI only with `serve --demo` or synthetic test fixtures. Do not inspect a user's live session.
- Never log token responses, callback query strings, authorization codes or library data.
- Never commit private data or credentials.
- This increment is read-only. Do not request mutation scopes or implement Spotify writes.
- Future writes require a proposal, explicit human approval and deterministic validation.
- Preserve human corrections; future decision history must be append-only.
- Run Ruff, MyPy, pytest and the tracked-private-path check before completing code changes.

