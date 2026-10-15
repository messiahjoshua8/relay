# ellict

Turn an existing application into installable agent skills from one command.

```bash
curl -fsSL https://eliolabs.xyz/install.sh | sh
ellict run . --publish-to ~/ellict-skills
```

The intended user is a coding agent. A person hands it [eliolabs.xyz](https://eliolabs.xyz) ([agent/index.html](agent/index.html)), and the agent installs ellict, runs it on the application in front of it, and reports what was published.

With `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `ELLICT_MODEL` set, a model reads the docs, manifests, and real `--help` output and answers what is worth a skill and how an agent would use it. Without a key, the agent answers the same questions itself: `ellict prompt .` writes them to `.ellict/prompt.md`, and `ellict run . --answer .ellict/answer.json` uses the reply. Either way, ellict drops any answer that names a command, flag, or route the repository does not have, and runs every example for real before publishing. Without a model or an answer it says so and uses static rules.

Ellict handles command-line applications in Python (`pyproject.toml`, `setup.py`), Node (`package.json` `bin`), Go (`main` packages in a module), and Rust (`[[bin]]` and `src/main.rs` across a Cargo workspace), plus OpenAPI operations and documented Make targets.

## Install

- `curl -fsSL https://eliolabs.xyz/install.sh | sh` downloads the source into `~/.local/share/ellict` and puts `ellict` on `~/.local/bin`, pinned to the Python 3.11+ it found. `ELLICT_SOURCE` overrides the archive URL.
- `pipx install git+https://github.com/messiahjoshua8/relay` works too; the package has no dependencies.
- From a checkout, `sh install.sh` points the command at that checkout.

`.github/workflows/site.yml` publishes `agent/index.html` and `install.sh` to GitHub Pages under the domain in `agent/CNAME`.

## Workflow

1. **Select.** A model, or the agent running ellict, reads the docs, manifests, interface, and help output and proposes skills, each with examples and small sample input files. Static rules are the fallback: console scripts, binaries, subcommands, OpenAPI operations, and documented Make targets. Installer commands such as `brew` and `pip` are not skills.
2. **Ground.** Invented commands, subcommands, routes, and flags are dropped. When the model proposes several skills on one command, ellict asks it a second, short question: which of these are the same job? Skills it groups together are merged into one, with the rest as examples. With `--answer` there is no model to ask, and a second skill on a command is merged unless its `distinct_job` says why it is different. Examples may only run the application's command, optionally piped through `cat`, `echo`, `printf`, `head`, `tail`, `grep`, `sort`, `uniq`, `wc`, `tr`, or `cut`, with no `;`, `&&`, command substitution, or writes outside the working directory.
3. **Run with no terminal attached.** Each example runs in an empty directory holding only its declared sample files, in a new session with no controlling terminal, stdin closed, and a timeout (`ELLICT_EXAMPLE_TIMEOUT`, default 15 seconds). An example that hangs, opens `/dev/tty`, or exits with the wrong code is removed. An example may declare a nonzero `exit` when that is success, as in a check mode.
4. **Build.** Write a skill directory: `SKILL.md`, `references/source.md`, `references/selection.md` for model-chosen skills, and a `scripts/run.sh` wrapper when the capability is a command.
5. **Validate.** Check the skill format, run each command with `--help`, and require at least one example that ran. HTTP operations and Make targets are recorded and not executed.
6. **Publish.** Copy approved packages to `.ellict/catalog/` and to local skill directories (`cursor`, `agents`, `claude`), and with `--publish-to` into a git repository you own, printing the `git add`, `commit`, and `push` you still run. The `registries` destination writes `.ellict/distribution/` for [skills.sh](https://www.skills.sh/docs/api) and [Smithery](https://registry.smithery.ai/skills).
7. **Maintain.** `ellict maintain` rebuilds only capabilities whose sources changed.

Every step appends a line to `.ellict/evidence.jsonl`. `ellict report` summarizes it.

## Commands

| Command | Effect |
| --- | --- |
| `ellict run [path]` | Select, ground, run examples, build, validate, publish |
| `ellict prompt [path]` | Write the selection questions to `.ellict/prompt.md` for an agent to answer |
| `ellict run [path] --answer FILE` | Use that answer instead of calling a model |
| `ellict extract [path]` | Print what static analysis found |
| `ellict maintain [path]` | Rebuild skills whose fingerprints changed |
| `ellict observe [path]` | Record which published skills are installed |
| `ellict report [path]` | Summarize the evidence log |

`ellict.json` at the application root selects destinations, `publishTo`, and `model`.

## Tests

```bash
PYTHONPATH=src:tests python3 -m unittest test_flow test_rules test_install test_gold
PYTHONPATH=src:tests python3 -m unittest test_live
```

`test_live` installs eight public applications for real: rich-cli, files-to-prompt, strip-tags, ttok, and toolong (Python), doctoc (Node), gron (Go), and sd (Rust). It needs `npm`, `go`, and `cargo` on `PATH` (or a Rust toolchain in `.ellict/toolchains/cargo`), plus network access. Its model half runs when a model is configured and scores the published skills against the jobs in [bench/gold.json](bench/gold.json). Every required job must be the job of exactly one skill, no job may be split across two skills, and toolong must publish nothing, because every one of its features needs a terminal.
