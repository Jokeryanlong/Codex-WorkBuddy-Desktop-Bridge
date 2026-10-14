---
name: workbuddy-agent-routing
description: Lead bounded work through WorkBuddy Desktop for research, code review, or artifact analysis. Use whenever work requires internet search, fact checking, code review, or analysis of a user-provided binary/package; keep Codex responsible for scope, evidence checks, and synthesis. Route code reviews to S1, S2, and S3; use free-prompt mode for binary reverse engineering.
---

# WorkBuddy Agent Routing

The bridge owns the four WorkBuddy identity prompts. Keep the identity scopes distinct.

## Runtime selection

1. Call `workbuddy_status` before dispatching any identity.
2. Require an open WorkBuddy Desktop window. Version 0.4.3 uses the desktop native conversation SDK. Require backend=workbuddy-desktop-native-conversation and desktop_realtime=true. Launch WorkBuddy with its native automation shortcut. Tasks appear in its own chat window. `workbuddy_doctor` validates native SDK readiness; a real prompt is still required to prove model-service access. Check `server_version` and `source_path` to detect a stale Codex MCP process.
3. If the WorkBuddy MCP tool is missing, transport is closed, the desktop account is unavailable, or discovery fails, report the specific missing connection stage. Do not replace the WorkBuddy Desktop worker with a manually launched CodeBuddy CLI/TUI.
4. Do not run multiple routes for the same task at the same time. Surface ordinary task-specific failures.

## WorkBuddy route

For every WorkBuddy task, pass only the task body and its identity key. Do not prepend, quote, or repeat the identity instructions:

```text
workbuddy_start(
  identity=<"online-search" | "S1" | "S2" | "S3">,
  prompt=<complete task only>,
  cwd=<working directory>,
  model="deepseek-v4.1-flash",
  reasoning_effort="low",
  timeout_seconds=300
)
```

For a first S1/S2/S3 review, also pass `review_target=<absolute reviewed file or directory>`. This binds the returned WorkBuddy session to that identity, working directory, and target.

Then call `workbuddy_wait` in intervals of at most 55 seconds until the task reaches a terminal state or 300 seconds have elapsed since dispatch. If the task is still non-terminal at that point, cancel it and report the timeout; do not launch another execution route.

- The bridge creates native WorkBuddy `working` tasks. Leave task titles to WorkBuddy's own automatic naming; never send or write a custom title.
- The bridge injects the registered identity instructions. Never include those instructions in `prompt`, even when the same identity is called repeatedly.
- The bridge uses native `ask` mode with `default` permissions for new and resumed dialogues. Send one bounded analysis question, read its answer, then decide the next step. Do not request an entire autonomous workflow in one turn.
- Run every identity from the same absolute path of the current project.
- Start S1, S2, and S3 before waiting for results. The bridge waits until each prompt is accepted, spaces prompt starts by at least one second, then runs accepted tasks concurrently with sessionId-isolated event channels.
- Return the worker findings to the main task; the orchestrator remains responsible for synthesis and final decisions.

## Binary and artifact analysis

When the user asks Codex to lead WorkBuddy on a PE/EXE or other shipped artifact:

1. Codex records the exact input path and SHA-256, classifies the carrier, and names one concrete question for the first worker pass. Keep the original untouched.
2. Use `workbuddy_start` with `identity` omitted; S1/S2/S3 are review roles, not reverse-engineering roles. Include the absolute `cwd`, artifact path, bounded scope, and requested report fields.
3. For offline static analysis, instruct WorkBuddy not to launch the binary or change files. Require observations with RVA/file offset and the basis (bytes, imports, strings, functions, callers/callees), then separate inference and unknowns.
4. Codex checks important claims with IDA or another available local analysis tool, then sends WorkBuddy one focused follow-up based on the verified evidence. Codex owns the final finding and report.
5. Ask mode is the default. Keep each turn scoped to one analysis question and inspect the returned evidence before continuing. Ask mode does not replace independent verification of proposed actions.

For a reverse-engineering task, a bounded prompt shape is:

```text
Analyze <absolute artifact path>, SHA-256 <measured hash>, offline and read-only.
Do not launch or modify the sample. Identify its PE/.NET/native type, entry point,
imports, strings/resources, and the strongest candidate for <specific question>.
For each conclusion, include an RVA/file offset and concrete evidence; separate
Observed, Inferred, and Unknown items. Do not guess missing symbols or addresses.
```

## Review continuation

When the user asks to review the same target again, treat it as a re-review unless they explicitly request a fresh or independent review.

```text
workbuddy_start(
  identity=<"S1" | "S2" | "S3">,
  prompt=<current re-review requirements only>,
  cwd=<same absolute project working directory>,
  review_target=<same absolute reviewed file or directory>,
  resume_review=true,
  model="deepseek-v4.1-flash",
  reasoning_effort="low",
  timeout_seconds=300
)
```

- Start S1, S2, and S3 re-reviews before waiting, as with first reviews. Each identity resumes its own most recently bound session for that target.
- The bridge loads the old WorkBuddy conversation and injects the mandatory re-review protocol. Do not repeat identity instructions or manually paste the previous findings.
- Every re-review must contain both a regression check of all prior findings and a complete incremental scan for newly introduced or previously missed issues.
- If a specific prior session is required, pass its ID as `resume_session_id=<sessionId>` instead of relying on automatic lookup.
- If the bridge reports that no matching old session exists, or that identity, cwd, target, or transcript validation failed, surface the failure. Never silently retry without `resume_review`, because that would create a new conversation.
- When the user explicitly asks for a fresh, independent, or clean-slate review, omit `resume_review` and start a new session with `review_target`.

## Result boundary

- After WorkBuddy returns a successful terminal result, treat that result as the sole research input for the main task.
- Do not call web search, fetch source URLs, use another research connector, spawn a research subagent, or dispatch another Worker to verify, supplement, or repeat the result.
- Only organize, translate, summarize, compare, and format the returned material. Do not add unsupported current facts from the orchestrator's own knowledge.
- If the returned material is incomplete, contradictory, or lacks evidence, state that limitation in the final answer instead of searching again.
- Perform another search only when the user explicitly asks for a new search or re-verification.

## Routing rules

- Internet research, current facts, fact checking, or source gathering: use `online-search`.
- Code review: use S1, S2, and S3 together unless the user explicitly requests one identity.
- Native/managed binary or package reversing: use the free-prompt mode and lead with one focused analysis question; Codex validates the returned evidence.
- A mixed research and code-review request may use `online-search` plus all three reviewers.
- Explicit identity names from the user override automatic routing.

## Free dialogue continuation

For ordinary dialogue, omit identity and review_target. Pass the previous successful session_id as resume_session_id and keep the same cwd. Verify the returned resumed flag and session_id. Review identities still require a bound review_target.

## Direct SDK control and progress

Call workbuddy_capabilities to discover verified controls. Use workbuddy_session for native state inspection without a model turn. While a task runs, read workbuddy_progress with the previous next_cursor; do not resubmit after a short wait timeout. Check finished, state, awaiting_user, history_gap and error before continuing. Only public answer deltas and tool statuses are exposed; reasoning and raw tool payloads are excluded. Generic internal tool dispatch is not exposed by the verified WorkBuddy SDK.
