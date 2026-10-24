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

## GitHub project tracking

- Maintain [Spotify-Playlist-Manager - Desenvolvimento](https://github.com/users/Luizhsampaio/projects/2)
  during work sessions: record meaningful ideas, agreed plans, decisions and progress without waiting
  for a separate request. Do not claim background monitoring.
- Check existing items before creating new ones. Keep vague ideas lightweight in Ideas; create
  repository issues linked to the project for concrete work. Avoid a task for every small UI tweak.
- Review relevant issues and dependencies before starting work and update them at meaningful
  milestones. Record partial delivery with evidence; do not confuse interface work with working automation.
- Keep agreed criteria, open questions, blockers and dependencies current. Preserve prior human
  decisions through dated comments rather than silently replacing them.
- Mark work Done only after validation, user acceptance and integration into main. A commit or push
  to develop alone is not completion. Backlog items do not authorize Spotify writes.
- Keep private account data and credentials out of issues and project items. If GitHub access fails,
  report the pending update explicitly rather than claiming the board is current.
