#
# src/supsrc/llm/prompts.py
#
"""
Stores prompt templates for LLM interactions.
"""

# --- Commit Message Generation ---

CONVENTIONAL_COMMIT_PROMPT_TEMPLATE = """
Analyze the following git diff and generate a concise, one-line commit message
in the Conventional Commits format. The diff is:

--- DIFF START ---
{diff}
--- DIFF END ---

The commit message should start with a type (e.g., feat, fix, docs, style, refactor,
test, chore), followed by an optional scope in parentheses, a colon, and then a
short description in the present tense. Do not include any other text, explanation,
or markdown formatting.

DO NOT provide a preamble, explanation, or anything else. ONLY provide the commit message.

Example: feat(api): add new endpoint for user profiles
"""

BASIC_COMMIT_PROMPT_TEMPLATE = """
Analyze the following git diff and generate a concise, one-line commit message
that summarizes the changes. The diff is:

--- DIFF START ---
{diff}
--- DIFF END ---

Do not include any other text, explanation, or markdown formatting. Just provide the
single-line commit message.

Example: Adding additional debug logging to path.py
"""



# --- Code Review ---

CODE_REVIEW_PROMPT_TEMPLATE = """
You are an expert code reviewer. Analyze the following git diff for potential issues.
Specifically check for:
- Obvious syntax errors or typos.
- Leftover debugging code like `print()` statements or `console.log()`.
- Hardcoded secrets, API keys, or passwords.
- Large, duplicated code blocks.
- Obvious logical errors that a human reviewer would spot immediately.

If you find a critical issue, respond with "VETO:" followed by a brief, one-sentence
explanation of the most critical issue.
If the code looks acceptable for a commit, respond with "OK".

Do not provide any other preamble or explanation. Your response must start with
either "VETO:" or "OK".

The diff to review is:
--- DIFF START ---
{diff}
--- DIFF END ---
"""


# --- Test Failure Analysis ---

TEST_FAILURE_ANALYSIS_PROMPT_TEMPLATE = """
You are a senior software engineer diagnosing a test failure. The following is the
output from a test command that failed.

--- TEST OUTPUT START ---
{output}
--- TEST OUTPUT END ---

Analyze this output and provide a concise, two-part response:
1.  **Cause:** A one-sentence summary of the most likely root cause of the failure.
2.  **Suggestion:** A one-sentence recommendation for how to fix it.

Format your response exactly as follows, with no other text:
Cause: <Your summary of the cause>
Suggestion: <Your recommendation for a fix>
"""


# --- Change Fragment Generation ---

CHANGE_FRAGMENT_PROMPT_TEMPLATE = """
Based on the following commit message and git diff, write a single-sentence
changelog entry suitable for a user-facing changelog. The sentence should be
in the past tense and describe the change from a user's perspective.

Do not include any other text, explanation, or markdown formatting.

Commit Message: {commit_message}

--- DIFF START ---
{diff}
--- DIFF END ---
"""

# 🧠📝
