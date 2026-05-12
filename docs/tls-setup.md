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

## Cowork rejects the connection even though `curl` works

This is its own beast — Claude Cowork (and similarly strict MCP clients)
sometimes refuse a connection that `curl` happily accepts. The list
below is the diagnostic ladder in order of cheapest-to-most-involved.
Work down it; the failure mode usually reveals itself by step 3 or 4.

### 1. Confirm the server itself is healthy

```bash
curl -v --cacert "$(mkcert -CAROOT)/rootCA.pem" https://localhost:8765/mcp \
  -H 'Accept: application/json,text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' 2>&1 | head -25
```

You want to see `* TLSv1.3 (IN), TLS handshake, Finished (20)` or similar
— meaning the cert chain validates and the handshake completes. If this
fails, fix the server before touching Cowork.

### 2. Watch for connection attempts at the network layer

In a separate terminal:

```bash
sudo tcpdump -ni lo0 'tcp port 8765'
```

Then attempt the connection in Cowork. Three outcomes tell you different
things:

- **No packets at all.** Cowork is rejecting the URL *before* opening a
  socket — typically App Transport Security (ATS) failing client-side
  on cert validation rules that go beyond keychain trust (e.g.,
  Certificate Transparency log enforcement; see step 5).
- **TCP SYN but no SYN-ACK reply.** The server isn't listening on the
  address Cowork is trying. Most often: IPv6 mismatch (step 4).
- **Full TLS handshake then RST/FIN.** Cowork reached the server but
  rejected the cert at a layer above the handshake — could be EKU
  checks, pinned root mismatch, or an internal Electron CA store
  ignoring the system keychain.

### 3. Check Cowork's actual error from the macOS unified log

```bash
log show --last 2m \
  --predicate 'process CONTAINS[c] "Claude" OR process CONTAINS[c] "Cowork"' \
  --info 2>/dev/null \
  | grep -iE 'tls|cert|secur|trust|ats|nsurl' | head -30
```

You may need `sudo` for some predicates. Common error fragments:

- `"NSURLErrorDomain Code=-1202"` — cert trust failure.
- `"is not CT qualified"` — Certificate Transparency rejection (mkcert
  certs are never CT-logged; see step 5).
- `"App Transport Security has blocked"` — ATS, period.
- `"hostname mismatch"` — SAN list doesn't include the URL hostname;
  re-issue with `mkcert <hostname> ...`.

### 4. IPv6 vs IPv4 binding mismatch

uvicorn bound to `127.0.0.1` listens on IPv4 only. `localhost` resolves
to *both* `::1` (IPv6) *and* `127.0.0.1` — and most macOS apps try IPv6
first. If the IPv6 connect fails fast, some clients give up rather than
fall back. Test:

```bash
# Same server but bound to IPv6 loopback:
uv run local-rag mcp --transport http --host ::1 --port 8765 \
  --cert ~/.config/local-rag/localhost+2.pem \
  --key  ~/.config/local-rag/localhost+2-key.pem
```

In Cowork, change the connector URL to `https://[::1]:8765/mcp`. The
mkcert SAN list includes `::1`, so the cert still validates.

If switching to IPv6 *also* fails, IPv6 isn't the problem — move on.

### 5. Certificate Transparency (ATS strictness)

Recent macOS versions enforce Certificate Transparency for ATS: the
cert must appear in a public CT log to be considered valid. mkcert
certs are issued by a local CA and **never appear in CT logs**, so any
client that strictly enforces CT will reject them even though the CA
is keychain-trusted. This shows up in step 2 as "no packets" — the
rejection happens before the socket opens.

You can't make mkcert certs CT-compliant; only real CA-issued certs
qualify. If this is the failure mode, you have two practical paths:

#### A. Tailscale Serve (recommended, if you have Tailscale)

`tailscale serve` proxies a local port through your tailnet with a real
Let's Encrypt cert on a `<machine>.<tailnet>.ts.net` URL. The cert is
CT-logged (satisfies ATS); the traffic stays on your tailnet (no third
party reads it); setup is ~1 command:

```bash
tailscale serve --https=443 --bg http://127.0.0.1:8765
```

Then point Cowork at `https://<machine>.<tailnet>.ts.net/mcp` (use
`tailscale status` to find the host name). The cert validates because
Let's Encrypt is in the public trust chain, and it's CT-logged because
Let's Encrypt publishes all issued certs.

Caveat: `tailscale serve` proxies the connection through the local
tailscaled process; the proxy terminates TLS at Tailscale's side, then
hits your local port over plain HTTP. That's fine because the plain-HTTP
hop never leaves the loopback interface — but it does mean `local-rag`
runs with `--transport http` and **no** `--cert`/`--key` flags when
fronted by Tailscale Serve.

#### B. Caddy reverse proxy with a tailnet cert (or your own domain)

If you'd rather keep `local-rag` serving TLS directly (e.g., to use the
bearer-token guard for non-loopback binds), put Caddy in front:

```caddyfile
mybox.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

Caddy auto-issues a Let's Encrypt cert via ACME for any real domain you
control, including Tailscale's `ts.net` domains with the right
configuration. Heavier than Tailscale Serve, but more flexible.

#### Don't bother

- **Cloudflare Tunnel / ngrok.** They provide ATS-compliant TLS, but the
  tunnel provider sees your traffic. Hard contradiction with the
  project's "no cloud APIs, no telemetry" rule.
- **Real CA + DNS challenge for `localhost`.** Public CAs won't issue
  certs for `localhost`; it'd require a real domain pointing at
  `127.0.0.1`, which is more setup than option A.

### 6. Electron's own cert store

If Cowork is an Electron app and uses Chromium's network stack on macOS,
it normally honors the system keychain. But some Electron builds
override this with `app.commandLine.appendSwitch('ignore-certificate-errors-spki-list', ...)`
or pin their own root CAs. There's nothing you can do from outside —
your only signal is unified-log entries (step 3) mentioning
`net::ERR_CERT_AUTHORITY_INVALID` or similar net-error codes. If you
see that, the only practical fix is option A above (use a publicly-
trusted cert via Tailscale Serve).

### 7. Confirm mkcert's CA is actually trusted at the system level

```bash
security verify-cert -c "$(mkcert -CAROOT)/rootCA.pem" 2>&1
# Expected: "...is a valid certificate"

security find-certificate -c "mkcert development CA" \
  /Library/Keychains/System.keychain ~/Library/Keychains/login.keychain-db \
  2>/dev/null | head -3
# Expected: a certificate block
```

If `verify-cert` doesn't say "valid certificate," `mkcert -install` didn't
complete. Re-run it (use `sudo` if prompted) and restart Cowork to pick
up the trust change.

### 8. Reissue the cert with explicit hostname coverage

If you've been connecting via `https://localhost:8765` and your cert was
issued with only `127.0.0.1` in the SAN list (or vice versa), some
clients fail with a hostname mismatch. Re-issue covering both:

```bash
cd ~/.config/local-rag
mkcert localhost 127.0.0.1 ::1
```

Then restart the server. The SAN list determines which URL hostnames
the cert is valid for.

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
