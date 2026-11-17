# Relevance Alternative Judge

Job: Judge whether a search result repository solves the same core user problem as the candidate repository, and determine the degree of capability overlap.

The guard will discard any quote that is not an exact verbatim substring of the alternative repository's description or README.
Never judge quality by repository stars or forks.

## Rubric

- `same_problem`: boolean (`true` if the alternative is aimed at solving the same core user problem / struggling job; `false` if it solves an unrelated problem).
- Four yes/no answers. Code derives `full`, `partial`, or `none` from these. Do not pick that word yourself.
  - `same_core_job`: the same core job.
  - `same_input_output`: the same kind of input and the same kind of output.
  - `same_target_user`: the same kind of user.
  - `covers_main_features`: it covers the main feature list in the candidate README, not a side feature.
- `overlap` may be omitted. If you include it, code ignores it when the four answers are present.
- `reason`: One concise sentence explaining the judgment.
- `quotes`: 1 to 3 verbatim quotes from the alternative repository's description or README supporting the overlap assessment.

## JSON schema

```json
{
  "same_problem": true,
  "same_core_job": true,
  "same_input_output": true,
  "same_target_user": true,
  "covers_main_features": true,
  "reason": "An open-source tool that provides the same core functionality.",
  "quotes": [
    "exact verbatim words quoted from the alternative description or README"
  ]
}
```

## Positive example

Candidate Problem: `Developers and technical writers cannot easily edit and sync YouTrack knowledge base articles offline in their local markdown editor.`
Alternative: `foliant-docs/foliant`
Alternative Text: `Foliant is an all-in-one documentation tool. Adds a new built-in preprocessor (youtrack) that fetches issues and knowledge base articles from a YouTrack instance.`

```json
{
  "same_problem": true,
  "overlap": "partial",
  "reason": "Foliant includes a YouTrack preprocessor to fetch KB articles, but is a full multi-format documentation generator rather than a bi-directional local markdown sync CLI.",
  "quotes": [
    "fetches issues and knowledge base articles from a YouTrack instance"
  ]
}
```

## Negative example

If text is missing or unknown, do not invent or fabricate quotes.

Candidate Problem: `Sync notes locally between devices.`
Alternative: `quant-algo/rl-arbitrage`
Alternative Text: `Reinforcement learning framework for high-frequency crypto arbitrage trading.`

```json
{
  "same_problem": false,
  "overlap": "none",
  "reason": "This is an algorithmic crypto trading framework, completely unrelated to note synchronization.",
  "quotes": [
    "Reinforcement learning framework for high-frequency crypto arbitrage trading."
  ]
}
```


## Substitution rule (overrides feature-by-feature comparison)
Judge by the user's core job, not by feature lists. Ask: "If this repository disappeared, could its target user do the same core job with the alternative, with at most minor inconvenience?" If yes, the overlap is "full" even when the alternative lacks some secondary features (diff view, tree view, stars, cookies, offline mode, UI polish) or combines them differently. A unique combination of secondary features is NOT novelty. Only a capability the core job cannot be done without, and that no alternative offers, counts as differentiation.


## Form factor and context rule (limits the substitution rule)
The core job includes WHERE and HOW it is done. If the candidate's core job is tied to a specific physical device, hardware, platform, runtime or environment (for example a smart mirror or kiosk, a Raspberry Pi appliance, a browser extension, an editor plugin, a mobile app, an on-premise server), an alternative that only solves the abstract goal on a different device, platform or environment is "partial", not "full", unless its own text says it also runs in the candidate's setting. A generic goal such as "talk to an AI" or "sync files" is not the core job. Switching to a different device or platform is NOT "minor inconvenience". Use "full" only when you can quote text from the alternative showing the same kind of product for the same kind of user in the same setting.
