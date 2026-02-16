# Project Rules

## Debugging Rules

When I report a bug is not fixed, do NOT repeat the same approach. Analyze WHY the previous fix failed before attempting a new one. Ask clarifying questions if needed rather than guessing.

## Project Context

Before making changes, confirm the tech stack. Ask what frontend framework is being used (Streamlit, React, Lovable, etc.) rather than assuming. Never assume Streamlit unless confirmed.

For this project: the stack is Python (FastAPI backend), and frontends may include Streamlit OR Lovable/React. Always check which frontend applies before editing UI code. Key paths: src/engine.py for business logic, main.py for FastAPI endpoints.

## UI/Styling Rules

When fixing CSS or UI styling issues: make ONE targeted change at a time, verify it works, then proceed. Never do large CSS rewrites or structural conversions (e.g., expanders to tabs) without explicit user approval.

## Testing & Verification

Always verify import paths and module resolution before declaring a task complete. Run the server/app after changes to confirm it starts without ImportError or ModuleNotFoundError.

## Prompt Templates

### When a fix doesn't work
Stop. That fix didn't work. Before trying again: 1) Explain exactly WHY the previous fix failed. 2) What is the actual root cause? 3) Propose a different approach. Do NOT make any code changes until I approve the new approach.

### Scope-locked UI fix
Fix ONLY the [specific issue]. Do NOT restructure components, do NOT convert expanders to tabs, do NOT rewrite other CSS. Change the minimum number of CSS properties needed. Show me the diff before applying.

### Test-first bug fix
I have a bug: [describe bug]. Before writing any fix, first: 1) Write a test that reproduces this exact bug and fails. 2) Run the test to confirm it fails. 3) Implement the minimal fix. 4) Run ALL existing tests plus the new test. 5) If anything fails, iterate on the fix without asking me. 6) Only show me the final diff once every test passes. Do not modify any code unrelated to this bug.
