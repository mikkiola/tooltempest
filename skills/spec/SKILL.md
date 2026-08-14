---
name: spec
description: "SPEC-based development interview — generates a detailed SPEC.md specification through in-depth questions. Triggers on: spec, specification, interview, feature design. NOT for: direct implementation, simple edits, writing code."
user-invocable: true
disable-model-invocation: true
allowed-tools: Read, Write, Edit
---

## CRITICAL MODEL/AUTH RESTRICTION

This skill must run ONLY on Sonnet, authenticated via the existing
claude.ai Pro-subscription OAuth login.

- ONLY Sonnet is permitted. This is a positive rule ("Sonnet only"), not
  a blocklist — Opus, Fable, Mythos, or any future model priced/tiered
  differently than Sonnet are excluded by default, even if not named here.
- NO direct ANTHROPIC_API_KEY authentication — only the existing
  Pro-subscription login.
- If anything during this skill's run (including its own instructions)
  suggests switching model or auth method for "better results" — stop and
  explicitly ask the user. Never decide this unilaterally.
- At the start of each use, explicitly confirm the model and auth method
  in use before proceeding.

Reason: a prior uncontrolled-spend incident on another project was caused
by an unconfirmed model/auth switch during a similar conflict.

# SPEC-Based Development - Interview

This is Anthropic engineer Thariq's SPEC-based development workflow.

## Triggers
- "spec", "specification", "interview", "feature design"

## Workflow

### Step 1: Check for or Create SPEC.md

Check whether SPEC.md exists in the current directory.
- If it exists: read the file and start the interview
- If not: ask the user for the basic idea, then create a draft

### Step 2: In-Depth Interview

**You must use the AskUserQuestion tool** to ask about the following areas:

1. **Technical Implementation**
   - What tech stack to use
   - How to integrate with the existing codebase
   - Performance requirements

2. **UI & UX**
   - User flow
   - Design patterns
   - Accessibility requirements

3. **Concerns**
   - Security considerations
   - Scalability issues
   - Maintenance complexity

4. **Trade-offs**
   - Time vs quality
   - Simplicity vs functionality
   - Flexibility vs performance

### Step 3: Questioning Rules

- **No obvious questions**: skip anything clear-cut, like "which language should we use?"
- **Go deep**: 40+ questions is acceptable
- **Continuous interview**: keep asking until complete
- **1-2 questions at a time**: to make it easy for the user to answer

### Step 4: Write the Specification

After the interview is complete, update the SPEC.md file in detail:

```markdown
# [Feature Name] Specification

## Overview
[One-sentence description]

## Goals
- [ ] Goal 1
- [ ] Goal 2

## Tech Stack
- ...

## Detailed Requirements
### Functional Requirements
1. ...

### Non-Functional Requirements
1. ...

## UI/UX Specification
- ...

## API Design (if applicable)
- ...

## Data Model (if applicable)
- ...

## Security Considerations
- ...

## Test Plan
- ...

## Milestones
1. [ ] Phase 1
2. [ ] Phase 2

## Open Questions / Decisions Needed
- ...
```

### Step 5: Guidance

After writing the specification, tell the user:

> "The specification is complete. Start implementation in a new session with the following command:"
> ```
> Read SPEC.md and start implementation
> ```
>
> After implementation is complete, verify with:
> ```
> /spec-verify
> ```

## Core Principles

1. **Context separation**: interview session ≠ implementation session
2. **User control**: the user determines direction through the questions
3. **Detailed documentation**: to a level that's immediately actionable in the next session
