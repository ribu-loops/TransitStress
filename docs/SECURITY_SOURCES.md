# Security research and rule rationale

Checked 19 October 2026. Primary guidance only; no claim that all AI-generated code fails or that a specific model has a measured failure rate on this application. These are failure classes worth preventing. SECURITY.md translates them into the narrower TransitStress design.

| Failure class | TransitStress response |
| --- | --- |
| Secrets copied into code/context, logs or public artifacts | No app secrets needed; named nonsecret environment settings only; redacted scans of files and history |
| Plausible but nonexistent/outdated packages | Verify package identity, lock versions, advisory lookup, review build hooks |
| Filename/widget validation treated as sufficient | Shared strict server-side parser and bounds; test direct calls |
| CSV/spreadsheet and HTML/script injection | Typed numerical exports, no arbitrary text in spreadsheet CSV, contextual encoding and controlled chart labels |
| Pickled cache or unsafe upload deserialization | CSV-only uploads, numeric artifacts, no untrusted cache restoration |
| Shared cache/stale UI data | Session-local upload state, complete public cache keys, explicit stale/result identity |
| Unbounded search/download/input | Check byte/row/baseline/grid budgets before work; keep hosted demo read-only |
| Arbitrary network/shell access | Fixed acquisition endpoints, verified redirects, fixed subprocess argument lists |
| Privileged CI and install scripts | Read-only CI permissions, pinned action SHAs, no secrets for PR execution |
| Agent follows hostile text or drifts into a rewrite | Treat external content as data; assigned tasks, one writer, independent reviews and preserved checks |

## Sources

1. **OWASP Secure Coding with AI:** https://cheatsheetseries.owasp.org/cheatsheets/Secure_Coding_with_AI_Cheat_Sheet.html . Covers leaked context, model-suggested dependencies, hostile external instructions and privileged build/CI changes. A security prompt alone is not verification.
2. **OWASP Secrets Management:** https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html . Credential lifecycle and exposure handling. Environment variables are not inherently forbidden; secret disclosure is the problem. TransitStress currently needs no credential.
3. **OWASP Input Validation:** https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html . Distinguishes checking a value's allowed meaning from encoding it safely at an output sink. Do not corrupt scientific numeric inputs with indiscriminate sanitization.
4. **OWASP File Upload:** https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html . Extensions/content types are insufficient, with size/path/storage constraints also needed. This app avoids public uploads and archive extraction.
5. **OWASP CSV Injection:** https://community.owasp.org/attacks/CSV_Injection . Spreadsheet programs can interpret untrusted cells as formulas; escaping is application-dependent. The scientific CSV contract avoids arbitrary text rather than claiming quotation is a universal fix.
6. **OWASP OS Command Injection Defense:** https://cheatsheetseries.owasp.org/cheatsheets/OS_Command_Injection_Defense_Cheat_Sheet.html . Avoid shell commands where possible and separate fixed commands from arguments. The scientific app needs none.
7. **OWASP SSRF Prevention:** https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html . Fixed destinations and redirect validation. Acquisition should not become a user-controlled URL-fetching service.
8. **Streamlit security reminders:** https://docs.streamlit.io/develop/concepts/connections/security-reminders . Cache serialization can execute unsafe pickle data; trust boundaries must be preserved even when using framework features.
9. **Streamlit cache architecture:** https://docs.streamlit.io/develop/concepts/architecture/caching . Distinguish shared cached resources/data from per-session state, and keep science-relevant keys complete.
10. **Streamlit configuration:** https://docs.streamlit.io/develop/api-reference/configuration/config.toml . Native upload/error/CORS/XSRF/usage-stat controls. Verify supported settings in the pinned installed version rather than disabling protections to fix UI issues.
11. **GitHub Actions secure use:** https://docs.github.com/en/actions/reference/security/secure-use . Least privilege, action pinning and untrusted pull-request input handling.
12. **Bandit, official project:** https://github.com/PyCQA/bandit . Python static security checks; review actual findings rather than interpreting a green scan as proof.
13. **pip-audit, official PyPA project:** https://github.com/pypa/pip-audit . Known dependency vulnerability checks; advisory access is a network operation distinct from offline tests.
14. **Gitleaks, official project:** https://github.com/gitleaks/gitleaks . File/history secret scanning with redacted output. Ignoring a credential file does not remove an older committed secret.

The byte/grid/baseline limits, local-only upload policy, scanner stages and evidence requirements are project design choices, not universal OWASP thresholds. They do not imply a completed penetration test or a secure implementation before development.
