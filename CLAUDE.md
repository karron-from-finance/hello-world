# CLAUDE.md

This file provides context for AI assistants working in this repository.

## Repository Overview

**Name:** hello-world
**Owner:** karron-from-finance
**Purpose:** Minimal placeholder/demonstration repository

This repository contains no application source code. It consists solely of a single `README.md` file with a short greeting message. There are no build systems, test frameworks, dependencies, or CI/CD pipelines configured.

## Repository Structure

```
hello-world/
├── README.md    # Project description (the only tracked file)
└── CLAUDE.md    # This file
```

## Development Workflows

### Branches

- `main` / `master` — primary branch with the initial commits
- Feature branches should follow the pattern `claude/<description>-<id>` when created by AI assistants

### Making Changes

Since this is a documentation-only repository, all changes will be to Markdown files. Follow standard git workflow:

```bash
# Create or switch to a working branch
git checkout -b claude/<description>-<id>

# Stage and commit changes
git add <files>
git commit -m "<clear, descriptive message>"

# Push to remote
git push -u origin <branch-name>
```

### Commit Messages

- Use the imperative mood ("Add ...", "Update ...", "Fix ...")
- Keep the subject line concise (under 72 characters)
- Describe *what* changed and *why*, not just *that* it changed

## Key Conventions

- **Language:** No programming language is used; content is pure Markdown
- **Formatting:** Standard GitHub-flavored Markdown (GFM)
- **No linting, testing, or build steps exist** — there is nothing to run
- **No dependencies** — no package manager files are present

## Git Configuration

- Remote origin: `http://local_proxy@127.0.0.1:43008/git/karron-from-finance/hello-world`
- Commit signing is enabled (SSH format)
- Author: Claude &lt;noreply@anthropic.com&gt; (for AI-generated commits)

## Notes for AI Assistants

- There is no code to analyze, test, or build in this repository
- Any new work should be added as Markdown files unless the repository's purpose expands
- When asked to add features or code, clarify with the user what language or framework they intend to use before creating files
- Keep changes minimal and scoped to what is explicitly requested
