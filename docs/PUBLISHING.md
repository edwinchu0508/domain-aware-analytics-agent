# Publishing this snapshot

The local publication was prepared in a fresh Git repository, without importing private development history. GitHub CLI was unavailable during preparation, so no authenticated account was selected, no public repository was created, and no push was performed. Source files are staged for inspection; the first commit is deferred until a real GitHub identity is available.

On Windows, install GitHub CLI if needed, reopen a shell, activate your Python environment, and authenticate:

```powershell
winget install --exact --id GitHub.cli
gh auth login
# From this repository root, with Python 3.11 and its dependencies available:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Publish.ps1
```

On other platforms, run the equivalent checked sequence: authenticate `gh`, verify release hashes, initialize a fresh repository if necessary, inspect/stage the publication, run `python -B scripts/scan_publication.py --staged`, commit, and create a **new** public repository with `gh repo create --public --source . --remote origin --push`. Do not force-push or overwrite an existing repository.

The Windows helper checks that the proposed name is not already used by the authenticated account, sets a repository-local verified GitHub username and GitHub-generated noreply email, checks release hashes and staged-file contents, commits and publishes. It does not read credential stores or print tokens. If any stage fails, inspect the resulting state before retrying; it does not overwrite an existing remote. No license has been selected.

If `python` resolves to a Windows Store alias or another environment, pass `-Python` with your Python 3.11 executable's full path. The helper validates that interpreter before release checking. An HTTP 404 from the repository-existence check is expected for a new repository; authentication, network and other API errors stop publication. This native-error handling also supports Windows PowerShell 5.1.

Generated outputs, local secrets, builds, private benchmark materials, raw traces, database dumps and personal files must remain excluded. The heuristic scan cannot replace human review of the staged file list. A local successful test run does not authorize a new paid evaluation.
