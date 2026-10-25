# 04 — GitHub integration

Build order matters: **Action → App → Marketplace.** The Action is two days of work
and produces a demo that lives inside GitHub's own security UI. The App is the
product. Doing the App first delays the first working demo by a week for no gain.

## Stage 1 — Composite Action + SARIF

Findings appear in **Security → Code scanning** on any repository, with no hosting,
no OAuth, no install flow.

```yaml
# .github/workflows/compliance.yml in the *customer's* repo
- uses: your-org/compliance-agent@v1
  with:
    frameworks: SOC2,GDPR
    api-url: https://eu-cell.example.com
  env:
    COMPLIANCE_TOKEN: ${{ secrets.COMPLIANCE_TOKEN }}
```

Then upload SARIF:

```yaml
- uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: compliance.sarif
```

Requirements: emit SARIF 2.1.0 with `ruleId = control_id`, `level` from severity,
and a `physicalLocation` region. Each `ruleId` needs a `reportingDescriptor` so the
Security tab shows the control title instead of an opaque ID.

Publish to the Actions Marketplace — free, fast, and it makes the project look alive.

## Stage 2 — GitHub App

An App, not an OAuth app: per-installation tokens, per-repo scoping, org installs,
webhooks, and Check Runs.

| Permission | Access | Reason |
|---|---|---|
| Contents | Read | clone |
| Metadata | Read | mandatory |
| Checks | Write | inline annotations |
| Pull requests | Write | sticky comment, optional fix PR |
| Actions | Read | skip audit when CI already ran (optional) |

Do **not** request `read:email`. You don't need it and it costs trust.

**Events:** `push`, `pull_request` (`opened`/`synchronize`), `installation`,
`installation_repositories`, `repository`.

**Auth:** App private key → RS256 JWT (≤10 min) → `POST
/app/installations/{id}/access_tokens` → 1h token. Cache per installation. Use
`@octokit/auth-app` rather than hand-rolling.

### Details that decide whether this works in practice

1. **Verify `X-Hub-Signature-256`** before parsing the body. Reject unsigned.
2. **Return 200 in under 10s.** Enqueue to Temporal; never clone inline. GitHub
   retries on timeout and you get duplicate audits.
3. **Dedupe on `X-GitHub-Delivery`.** Belt and braces with `WorkflowId =
   audit:{repo}:{commit}`, which makes duplicate delivery a no-op.
4. **Ignore `deleted: true`, tags, and non-default branches** on `push`.
5. **PR mode is diff-aware**: audit only changed files plus one hop of call-graph
   neighbours. This is what makes it feel instant and keeps cost sane.
6. **Sticky comment, not a new one per push.** Find your previous comment by marker
   and update it. Posting a fresh comment every push gets the App uninstalled.
7. **Check Run annotations cap at 50 per request.** Batch, and link `details_url` to
   the cell dashboard for the rest.
8. **Never push to the default branch.** Fix PRs go to `compliance-fix/<audit>` as
   `draft: true`, via the Git Data API, never force-pushed.

### Rate limits

5,000 requests/hour per installation. Use conditional requests (ETag) and GraphQL for
bulk listing. A 12-file audit is ~6 requests; the clone is the only heavy call.

## Stage 3 — Evidence in the repository

The customer's own repo becomes the append-only audit trail:

- commit `.compliance/audit-<timestamp>.json` + a detached Ed25519 signature
- store the attestation as a **Release asset**
- publish the public key at `.compliance/pubkey.pem`
- README badge via a shields.io endpoint reading the last score

This is what makes a residency claim checkable by a third party, and it costs about
40 lines.

## Stage 4 — Marketplace

Verified publisher domain, privacy policy, ToS, pricing plans, 200×200 logo,
screenshots, demo video, working webhook, and an install that completes in under a
minute. Review takes 1–2 weeks.

Ship a **GitHub App Manifest** (`.github/app-manifest.json`) so self-hosted customers
can create their own App in one click — that is how an enterprise runs the product in
its own region.

## Repository hygiene for this project

`LICENSE` (Apache-2.0), `SECURITY.md`, `CONTRIBUTING.md`, `CODEOWNERS`, issue and PR
templates, `.env.example`, branch protection, Dependabot, Conventional Commits.

CI must **never** require a GPU. The model sits behind `LLMConfig`, and
`MOCK_LLM=1` makes the whole suite runnable on a stock GitHub runner — otherwise
contributors cannot run the tests.
