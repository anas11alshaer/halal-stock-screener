# ADR 0008: Modular multi-provider screening

- Date: 2026-08-26
- Status: accepted
- Supersedes: ADR 0002's two-source resolution and ADR 0004's fixed composition

## Context

The service directly constructed Musaffa and Zoya, stored provider-specific history
columns, guessed equity when Yahoo failed, and cached unrecognized pages as not covered.
Adding or replacing a source required changes throughout the application.

## Options

1. Keep the fixed two-source design and add more branches.
2. Replace the service with a general LLM/browser agent.
3. Keep deterministic screening and introduce small provider, reviewer, and channel
   contracts selected by configuration.

## Decision

Use option 3. Resolve a canonical security once, fan out to every configured capable
provider, retain evidence and operational failures, and vote only confirmed verdicts.
A unique majority wins; any top-count tie resolves to `NOT_HALAL`; one confirmed source
is provisional. Gemini may review bounded parser-failure text but cannot browse or
return evidence absent from that text.

The private-free default enables Musaffa public pages, Zoya public stock pages, and
Daleel. Keyed free-tier providers are optional and quota failures cannot block others.
Image extraction is also selected by plugin path; Gemini is one implementation.

## Consequences

- Providers and Telegram can be replaced by changing `module:Class` configuration.
- Cache and history records are keyed by arbitrary provider IDs.
- Operational failures are visible and never vote or become compliance cache entries.
- A two-source disagreement is now Not Halal because it is a tie.
- External provider terms, schemas, and free quotas remain operational dependencies.

## Follow-up

- Run low-rate live canaries from the deployment environment.
- Prefer official licensed APIs if a private installation later becomes public.
