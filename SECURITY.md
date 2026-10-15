**English** | [Русский](SECURITY.ru.md)

# Security

Report a vulnerability privately: [open a security advisory](https://github.com/octrow/greenkeep/security/advisories/new). Do not open a public issue for it.

You get a first answer within 7 days. Fixes go into the next release; only the latest release is supported.

greenkeep runs on your machine and changes only the repositories you pass to it. It clones each one into `~/.cache/greenkeep`, keeps its state in `~/.local/share/greenkeep/state.db`, and talks to GitHub through your `gh` login. The OmniRoute key is read from `OMNIROUTE_API_KEY` or `~/.omniroute/.env` and sent only to `OMNIROUTE_URL` (default `http://localhost:20128`). Code from the tended repo goes to that endpoint as part of the prompt, so point it only at a model provider you trust with that code. Without `--push` nothing leaves the machine except the LLM calls.

Release files carry build provenance: `gh attestation verify <file> -R octrow/greenkeep`.
