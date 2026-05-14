# Running local-rag on a schedule and in the background

Two separate lifecycle decisions sit on top of `local-rag`:

1. **Indexing cadence** — how often does the index refresh from disk?
2. **HTTP server lifetime** — does the server run only when you start it,
   or stay up across reboots?

You can mix and match. The two processes are safe to run concurrently —
LanceDB uses snapshot semantics, so the search server sees newly-indexed
rows on its next query without a restart.

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
makes frequent indexing essentially free. Pick whichever scheduler fits.

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

    <key>StartInterval</key><integer>1800</integer>  <!-- every 30 minutes -->

    <key>StandardOutPath</key>
    <string>/Users/<you>/.local/state/local-rag/index.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/<you>/.local/state/local-rag/index.log</string>
</dict>
</plist>
```

Load it:

```bash
mkdir -p ~/.local/state/local-rag
launchctl load ~/Library/LaunchAgents/com.<you>.local-rag-index.plist
```

Use `StartCalendarInterval` instead of `StartInterval` if you want
specific times (e.g. every hour on the hour). See `man launchd.plist`.

---

## HTTP server lifetime

Only relevant if you're using `--transport http` (Cowork or any
non-stdio MCP client). The stdio transport is short-lived and
managed by the MCP client itself; nothing to do.

### TLS for Cowork (applies to all three options below)

Cowork requires `https://` URLs; the rest of this section assumes you've
already issued a local cert. See [tls-setup.md](tls-setup.md) for the
full walkthrough (mkcert, file permissions, rotation, openssl fallback,
verification, troubleshooting). The short version:

```bash
brew install mkcert && mkcert -install
mkdir -p ~/.config/local-rag && cd $_
mkcert localhost 127.0.0.1 ::1     # writes localhost+2.pem and localhost+2-key.pem
```

Pass `--cert` and `--key` to any `local-rag mcp --transport http` invocation
below. The examples assume the standard cert paths from `~/.config/local-rag/`.
If you don't need TLS (Cowork isn't your target, you're just smoke-testing),
drop the two flags — plain HTTP still works for clients that accept it.

### Option 1 — `nohup` (current session only)

```bash
mkdir -p ~/.local/state/local-rag
nohup uv run local-rag mcp --transport http --port 8765 \
  --cert ~/.config/local-rag/localhost+2.pem \
  --key  ~/.config/local-rag/localhost+2-key.pem \
  > ~/.local/state/local-rag/http.log 2>&1 &
disown
```

Survives terminal close. Dies on reboot. Stop with:

```bash
lsof -i :8765                  # find PID
kill <PID>
```

### Option 2 — launchd (recommended; survives reboot, auto-restarts)

Save as `~/Library/LaunchAgents/com.<you>.local-rag-mcp.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.<you>.local-rag-mcp</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/<you>/.local/bin/uv</string>
        <string>run</string>
        <string>local-rag</string>
        <string>mcp</string>
        <string>--transport</string><string>http</string>
        <string>--port</string><string>8765</string>
        <!-- TLS (required for Cowork; remove this block for plain HTTP) -->
        <string>--cert</string><string>/Users/<you>/.config/local-rag/localhost+2.pem</string>
        <string>--key</string><string>/Users/<you>/.config/local-rag/localhost+2-key.pem</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/<you>/Downloads/src/local-rag</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/Users/<you>/.local/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>

    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>

    <key>StandardOutPath</key>
    <string>/Users/<you>/.local/state/local-rag/http.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/<you>/.local/state/local-rag/http.log</string>
</dict>
</plist>
```

Load:

```bash
mkdir -p ~/.local/state/local-rag
launchctl load ~/Library/LaunchAgents/com.<you>.local-rag-mcp.plist
launchctl list | grep local-rag      # verify
lsof -i :8765                         # verify listening
```

`KeepAlive=true` means launchd restarts the process if it crashes.
Stop / reload:

```bash
launchctl unload ~/Library/LaunchAgents/com.<you>.local-rag-mcp.plist
launchctl load   ~/Library/LaunchAgents/com.<you>.local-rag-mcp.plist
```

#### With a bearer token

If you're binding off loopback (e.g. for a Mac mini on your LAN),
the CLI refuses to start without a token. Add the token to
`EnvironmentVariables` in the plist — don't put it in
`ProgramArguments` where `ps` can see it:

```xml
<key>EnvironmentVariables</key>
<dict>
    <key>PATH</key>
    <string>/Users/<you>/.local/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>LOCAL_RAG_MCP_TOKEN</key>
    <string>your-long-random-token-here</string>
</dict>
```

And in `ProgramArguments`, change to `--host 0.0.0.0`. Generate the
token once with `openssl rand -hex 32`.

### Option 3 — tmux

If you already work in tmux:

```bash
tmux new -d -s local-rag-mcp \
  'uv run local-rag mcp --transport http --port 8765 \
     --cert ~/.config/local-rag/localhost+2.pem \
     --key  ~/.config/local-rag/localhost+2-key.pem'
tmux attach -t local-rag-mcp           # see live output
# Ctrl-b d to detach
```

Survives terminal close. Dies on reboot.

---

## Combining both

Running launchd indexing **and** a launchd HTTP server is the typical
setup for "always on, always fresh." Both plists installed and loaded;
LanceDB handles the concurrency. Search results will reflect indexed
content from up to ~30 minutes ago (or whatever your `StartInterval`
is).

If you want fresher search results without polling, the project spec
defers a `watchdog`-based filesystem-event watcher as a future slice
(see [docs/specs/local-rag.md](specs/local-rag.md) → "Decisions
deferred"). Not implemented in v1.

---

## Log management

The launchd plists above append forever to a single log file. For a
long-running service, set up rotation:

- **macOS** ships `newsyslog` (`/etc/newsyslog.conf` and
  `/etc/newsyslog.d/`). Drop a config in
  `/etc/newsyslog.d/local-rag.conf`:

  ```
  # logfilename                                                          [owner:group]   mode count size when  flags [/pid_file] [sig_num]
  /Users/<you>/.local/state/local-rag/*.log                                   644  7     1000 *     N
  ```

  Rotates when any log exceeds 1000 KB; keeps 7 generations. `newsyslog`
  runs out of `launchd` once a day by default; no action needed beyond
  the config file.

---

## Troubleshooting

**`launchctl load` says "service already loaded"** — `launchctl unload`
first, then `load`.

**Port already in use** — `lsof -i :8765` to find the squatter.
Often a previous `nohup` instance you forgot about.

**launchd job won't start** — check `~/.local/state/local-rag/http.log`
(or wherever `StandardErrorPath` points). The most common cause is
missing `EnvironmentVariables.PATH`, which makes `uv` unable to find
its own subcommands.

**Bearer-token errors after a config change** — the launchd process
caches its environment from `load` time. After editing the plist's
token, `unload` + `load` to pick up the new value.

**Cowork says "connection refused"** — confirm the server is bound to
loopback (not just a unix socket): `lsof -i :8765` should show
`*:8765 (LISTEN)` or `127.0.0.1:8765 (LISTEN)`. Restart Cowork after
adding the server URL; some versions cache the connector list.
