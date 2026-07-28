# Keeping the index fresh

The index does **not** update itself — nothing watches the filesystem, and
the MCP server never re-indexes. Search results are only as fresh as the
last `local-rag index` run. The stdio servers that Claude Code and Cowork
spawn read a fresh LanceDB snapshot per query, so a completed index run is
visible immediately — no restarts needed, and the indexer is safe to run
while a server is up.

All examples are macOS-native. Paths use `<you>` as a placeholder for
your macOS short username (the directory under `/Users/`); substitute
your own everywhere it appears. Examples assume the repo is at
`/Users/<you>/Downloads/src/local-rag` and `uv` is at
`/Users/<you>/.local/bin/uv` — adjust for your setup (`whoami` gives
the username, `which uv` gives the binary path). Launchd plists and
cron don't expand `~`, so the absolute form is required there even
though `~/...` works fine in interactive shells.

---

## Indexing cadence

The incremental design (SHA-256 file hashes; unchanged files never re-embed)
makes frequent indexing essentially free. Pick whichever scheduler fits:

| Option  | Freshness           | Survives reboot           | Setup      |
|---------|---------------------|---------------------------|------------|
| Manual  | when you remember   | n/a                       | none       |
| cron    | fixed interval      | yes; missed runs skipped  | crontab    |
| launchd | fixed interval      | yes; catches up on wake   | one plist  |

launchd is the recommended default on macOS — unlike cron it runs a
missed interval after sleep/wake instead of silently skipping it.

**Expect the first scheduled run to take a while** (minutes, not seconds)
if the sources haven't been indexed recently — every new or changed file
is embedded through Ollama. Steady-state runs where little changed
complete in seconds.

### Option 1 — Manual

```bash
uv run local-rag index
```

Run when you remember. No setup.

### Option 2 — cron (every 30 min)

```bash
crontab -e
```

Add:

```cron
*/30 * * * * cd /Users/<you>/Downloads/src/local-rag && /Users/<you>/.local/bin/uv run local-rag index >> /Users/<you>/.local/state/local-rag/cron.log 2>&1
```

`mkdir -p ~/.local/state/local-rag` for the log dir. cron's `PATH` is
minimal — always use the absolute path to `uv`.

### Option 3 — launchd (Mac-native scheduler)

More resilient than cron across sleep / reboot. Save as
`~/Library/LaunchAgents/com.<you>.local-rag-index.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.<you>.local-rag-index</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/<you>/.local/bin/uv</string>
        <string>run</string>
        <string>local-rag</string>
        <string>index</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/<you>/Downloads/src/local-rag</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/Users/<you>/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>

    <!-- every 30 minutes -->
    <key>StartInterval</key><integer>1800</integer>

    <key>StandardOutPath</key>
    <string>/Users/<you>/.local/state/local-rag/index.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/<you>/.local/state/local-rag/index.log</string>
</dict>
</plist>
```

`EnvironmentVariables.PATH` matters: launchd jobs don't inherit your
shell's PATH, and `uv` needs to find Python and its own subcommands
(see [Troubleshooting](#troubleshooting)).

Load it and trigger a first run immediately instead of waiting for the
interval:

```bash
mkdir -p ~/.local/state/local-rag
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.<you>.local-rag-index.plist
launchctl kickstart gui/$(id -u)/com.<you>.local-rag-index
```

(`launchctl load` is the legacy spelling of `bootstrap`; both work.)

Verify:

```bash
launchctl list | grep local-rag      # job is registered; second column is last exit code
tail -f ~/.local/state/local-rag/index.log
```

To stop it:

```bash
launchctl bootout gui/$(id -u)/com.<you>.local-rag-index
```

Use `StartCalendarInterval` instead of `StartInterval` if you want
specific times (e.g. every hour on the hour). See `man launchd.plist`.

If you want fresher search results without polling, the project spec
defers a `watchdog`-based filesystem-event watcher as a future slice
(see [docs/specs/local-rag.md](specs/local-rag.md) → "Decisions
deferred"). Not implemented in v1.

---

## Model memory and keep-alive

local-rag never tells Ollama how long to keep `bge-m3` in memory, so
Ollama's own `keep_alive` policy decides — **5 minutes** by default.
This interacts with your indexing schedule: with the 30-minute examples
above, each run loads the model, the model unloads 5 minutes later, and
the next run cold-loads it again — roughly 48 load/unload cycles a day.
The same applies to search: an MCP query more than 5 minutes after the
last embed pays the cold-load latency.

Whether that matters is a trade-off only you can make:

- **Leave the default (model unloads on idle).** Reasonable if RAM is
  tight or the same Ollama serves large chat models you want evicted
  promptly. The cost is small — `bge-m3` is ~665 MB at F16 and
  cold-loads from SSD in a few seconds.
- **Keep the model resident.** Set `OLLAMA_KEEP_ALIVE` to something
  above your indexing interval (e.g. `45m` for a 30-minute schedule,
  or `-1` for never unload). Each scheduled run then re-arms the timer,
  so the model stays warm and interactive search never cold-starts.
  For the macOS Ollama app:

  ```bash
  launchctl setenv OLLAMA_KEEP_ALIVE 45m
  # then quit and reopen Ollama.app
  ```

  For a manually run server, `OLLAMA_KEEP_ALIVE=45m ollama serve`.

Note `OLLAMA_KEEP_ALIVE` is server-wide — it affects every model that
Ollama serves, not just `bge-m3`.

---

## What about the server?

Nothing to schedule. The MCP server runs over stdio and is spawned and
managed by the client (Claude Code or Claude Desktop/Cowork) per session
— see [claude-integration.md](claude-integration.md) for wiring it up.
An HTTP/HTTPS transport existed in earlier revisions but was removed
once both clients settled on stdio; if you need HTTP for another MCP
client, front the stdio server with a generic adapter such as
`mcp-proxy`, or resurrect the transport from git history.

---

## Log management

The launchd plist above appends forever to a single log file. For a
long-running setup, add rotation:

- **macOS** ships `newsyslog` (`/etc/newsyslog.conf` and
  `/etc/newsyslog.d/`). Drop a config in
  `/etc/newsyslog.d/local-rag.conf`:

  ```text
  # logfilename                                                          [owner:group]   mode count size when  flags [/pid_file] [sig_num]
  /Users/<you>/.local/state/local-rag/*.log                                   644  7     1000 *     N
  ```

  Rotates when any log exceeds 1000 KB; keeps 7 generations. `newsyslog`
  runs out of `launchd` once a day by default; no action needed beyond
  the config file.

---

## Troubleshooting

**`launchctl bootstrap` says "service already loaded" (or `load` does)**
— `launchctl bootout` (or `unload`) first, then re-load.

**launchd job won't start** — check `~/.local/state/local-rag/index.log`
(or wherever `StandardErrorPath` points). The most common cause is
missing `EnvironmentVariables.PATH`, which makes `uv` unable to find
its own subcommands.

**Runs happen but the index looks stale** — confirm the job's
`WorkingDirectory` points at your clone (the config is resolved from
there unless `LOCAL_RAG_CONFIG` is set), then run
`uv run local-rag list` and compare counts before/after a manual
`uv run local-rag index`.

**`Too many open files (os error 24)` in the log** — usually paired with
`Cannot open index on column 'text' ... Skipping index merge`. Merging FTS
deltas opens every index partition file at once (~290 for a 37k-chunk table,
growing with corpus size and indexing history), while launchd and cron hand
their jobs a 256 soft limit. `local-rag index` raises its own soft limit at
startup, so this should not appear — note it is a *silent quality* failure,
not a crash: the run still exits 0, but BM25 stays blind to the newest chunks
until a later merge succeeds.

If you do see it, the process was refused the raise (an unusually low
`kern.maxfilesperproc`, or a container FD cap). Grant the limit in the
scheduler instead — for launchd, add to the plist:

```xml
<key>SoftResourceLimits</key>
<dict>
    <key>NumberOfFiles</key><integer>65536</integer>
</dict>
```

For cron, prefix the command with `ulimit -Sn 65536;`. Confirm what a
running job actually got with
`launchctl print gui/$(id -u)/com.<you>.local-rag-index | grep -A3 'resource limits'`.

**The database directory keeps growing** — LanceDB retains superseded table
versions, and every indexing run creates one. `optimize()` prunes versions
older than two days automatically (`_VERSION_RETENTION` in `store.py`), so
size plateaus at roughly two days of churn. A store that predates that
behaviour reclaims the backlog on its next few runs.

**Every scheduled run logs "embedder unreachable"** — Ollama isn't
running, or doesn't have `bge-m3` pulled. `ollama list` and
`curl http://localhost:11434/api/tags` are the usual diagnostics.
