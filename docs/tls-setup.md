# Setting up TLS for the HTTP transport

`local-rag mcp --transport http` can serve over HTTPS using a user-supplied
certificate and key. This is **required** for Claude Cowork (which rejects
`http://` URLs even for loopback) and recommended for any non-loopback bind.

This is the single source of truth for TLS setup. The Cowork recipes in
the [README](../README.md) and
[claude-integration.md](claude-integration.md) link here for the details.

---

## Why TLS at all? Isn't loopback already private?

Loopback traffic on macOS doesn't leave the kernel — there's no wire to
sniff. The TLS requirement comes from Cowork's connector layer, not the
network. Cowork enforces HTTPS for *all* MCP servers, the same way modern
browsers refuse mixed-content fetches: it's a categorical safety rule, not
a per-connection threat assessment. We comply by serving HTTPS.

For non-loopback binds (e.g. a Mac mini on your LAN), TLS *is* doing
real work — preventing passive sniffing on your network — and the
project's "bind off-loopback requires a token" guard pairs nicely with
serving HTTPS to that token-bearing client.

---

## Recommended path: mkcert

[`mkcert`](https://github.com/FiloSottile/mkcert) is purpose-built for
this exact scenario. It installs a local certificate authority into your
system trust store, then issues certs that chain to that CA — so anything
on your Mac that uses the system keychain (Cowork, Safari, curl, …)
trusts them automatically. No browser warnings, no manual cert
imports.

### One-time setup

```bash
brew install mkcert
mkcert -install
```

`mkcert -install` writes a local root CA into your macOS keychain and
the system trust store. You'll be prompted for your password once. To
remove it later: `mkcert -uninstall`.

### Issue a cert for local-rag

Pick a directory to store the cert files. `~/.config/local-rag/` is a
sensible default — it sits next to your `config.toml`:

```bash
mkdir -p ~/.config/local-rag
cd ~/.config/local-rag
mkcert localhost 127.0.0.1 ::1
```

This writes two files:

- `localhost+2.pem` — the certificate (public; fine to share/log).
- `localhost+2-key.pem` — the private key (**never share, never log**).

The `+2` in the filenames is mkcert's count of SAN entries (subject
alternative names). The names you pass become the SAN list, so the cert
is valid for connecting via `localhost`, `127.0.0.1`, or `::1`.

If you also want the cert to work over LAN (e.g. for a Mac mini at
`silver.local`), include it:

```bash
mkcert localhost 127.0.0.1 ::1 silver.local 192.168.1.42
```

You can re-run `mkcert` with different SANs at any time; it overwrites
the existing files.

### File permissions

The key file should not be world-readable. mkcert writes it as `600` by
default. Confirm:

```bash
ls -la ~/.config/local-rag/localhost+2-key.pem
# -rw-------  ...
```

If it's `644`, tighten it: `chmod 600 ~/.config/local-rag/localhost+2-key.pem`.

---

## Using the cert with local-rag

### Ad-hoc

```bash
uv run local-rag mcp --transport http --port 8765 \
  --cert ~/.config/local-rag/localhost+2.pem \
  --key  ~/.config/local-rag/localhost+2-key.pem
```

The server is then reachable at `https://localhost:8765/mcp`.

### Long-running via launchd

Drop the same flags into the `ProgramArguments` array of your
LaunchAgent plist. See
[deployment.md → Option 2 (launchd)](deployment.md#option-2--launchd-recommended-survives-reboot-auto-restarts)
for the full plist.

### Combined with bearer-token auth (non-loopback)

For a LAN bind, both TLS and a bearer token are required:

```bash
export LOCAL_RAG_MCP_TOKEN="$(openssl rand -hex 32)"
uv run local-rag mcp --transport http \
  --host 0.0.0.0 --port 8765 \
  --cert ~/.config/local-rag/localhost+2.pem \
  --key  ~/.config/local-rag/localhost+2-key.pem
```

Clients then connect to `https://<lan-host>:8765/mcp` with header
`Authorization: Bearer $LOCAL_RAG_MCP_TOKEN`. The two layers are
complementary: TLS encrypts the wire; the token gates access.

---

## Verifying the cert works

```bash
# 1. Reachable + TLS handshake completes (no -k means full validation)
curl -sv https://localhost:8765/mcp \
  -H 'Accept: application/json,text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' 2>&1 | head -30

# 2. Inspect the cert directly
openssl s_client -connect localhost:8765 -servername localhost -showcerts </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates -ext subjectAltName

# 3. From a browser (if Cowork keeps saying 'not secure')
#    Visit https://localhost:8765/mcp — should NOT show a warning page.
#    If it does, mkcert -install didn't take or you skipped it.
```

`curl` without `-k` succeeds → the cert is valid and trusted. `curl -k`
succeeds but `curl` alone fails → the cert is *served* but not
*trusted*; re-run `mkcert -install`.

---

## Cert rotation

mkcert certs expire **2 years and 3 months** after issuance by default.
The local-rag server will refuse new TLS handshakes once the cert
expires, and clients will see "certificate has expired" errors.

To rotate:

```bash
cd ~/.config/local-rag
mkcert localhost 127.0.0.1 ::1     # overwrites with a fresh cert
```

Then restart the server so uvicorn re-reads the cert file:

```bash
launchctl unload ~/Library/LaunchAgents/com.bbirkinbine.local-rag-mcp.plist
launchctl load   ~/Library/LaunchAgents/com.bbirkinbine.local-rag-mcp.plist
```

mkcert's own root CA expires in **10 years**. If that ever lapses,
`mkcert -uninstall` then `mkcert -install` will issue a fresh root, and
you'll need to re-issue any certs that chain to it. Worth a calendar
reminder ~9 years out, or skip it and reactively re-run when something
breaks.

---

## Alternative: openssl self-signed (for testing only)

If you can't install mkcert (locked-down org Mac, Linux dev box,
whatever), you can generate a one-off self-signed cert with `openssl`:

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout ~/.config/local-rag/test.key \
  -out    ~/.config/local-rag/test.crt \
  -subj '/CN=localhost' \
  -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1'
```

**Caveat:** the resulting cert is not in your system trust store, so
Cowork will reject the connection unless you manually import the cert
as a trusted root in Keychain Access (Security → Certificates →
double-click → "Always Trust"). mkcert automates all of that. Only use
openssl for command-line testing where you control the client (`curl
-k`, etc.).

---

## Troubleshooting

**`curl: (60) SSL certificate problem: unable to get local issuer
certificate`** — The CA isn't installed in your trust store. Run
`mkcert -install` and try again. If `mkcert -install` was already run
on this machine but you're connecting from another box (e.g. you
issued the cert on one Mac, then tried to use it from a different
machine over LAN), copy mkcert's root CA to the other machine and
install it there too: `mkcert -CAROOT` shows the path on the issuing
machine.

**Cowork says "connection failed" or "certificate not trusted"** —
Cowork uses the macOS system keychain. If `mkcert -install` ran
successfully and the keychain shows a "mkcert development CA" entry
under System Roots (Keychain Access → System Roots → search "mkcert"),
the cert is trusted. If not, `mkcert -install` didn't finish — re-run
with `sudo` if needed. After fixing trust, **restart Cowork** — it
caches the trust state at startup.

**`SSL: CERTIFICATE_VERIFY_FAILED, Hostname mismatch`** — the SAN
list on the cert doesn't include the hostname you're connecting to.
Re-issue: `mkcert localhost 127.0.0.1 ::1 <other-name>`.

**`uvicorn: error: argument --ssl-certfile: file does not exist`** —
That's local-rag's own validation, not uvicorn's. Check the `--cert`
path; remember `~` doesn't expand inside plist `<string>` values —
use the absolute path.

**Cert expired** — `mkcert localhost 127.0.0.1 ::1` to re-issue, then
restart the local-rag server. The launchd `KeepAlive=true` setting
will keep crash-looping on the expired cert until you fix it; check
`~/.local/state/local-rag/http.log` for the underlying error.

**"This connection is not private" in Safari but everything else
works** — Safari sometimes caches the previous cert-not-trusted
decision per-origin. Open Keychain Access, search for "localhost",
delete any stale entries, then restart Safari.

---

## What we don't do

Per the [slice-09 spec](specs/slice-09-https.md) non-goals:

- **No ACME / Let's Encrypt integration.** Public certs aren't useful
  for a loopback service, and a real internet-facing local-rag goes
  against the project's privacy-first design.
- **No mTLS / client certs.** Bearer token + server TLS is the auth
  model.
- **No auto-cert-generation.** mkcert is good enough; we don't shell
  out to it. You bring the files; we serve them.
- **No HTTP→HTTPS redirect.** uvicorn binds one protocol at a time.
  Pick TLS or not at server-start time.
- **No config-file TLS fields.** CLI flags only — keeps `config.toml`
  focused on what's indexed, not how it's served.
