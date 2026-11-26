# Security

## Reporting a problem

Please report security problems privately, not in a public issue. On this repository's GitHub page, open the
**Security** tab and choose **Report a vulnerability**. Describe what you found and how to reproduce it.

This is a free project looked after in spare time, so there is no fixed response time, but security reports are read
first. Only the latest version is fixed; there are no maintained older releases.

## What NPC Forge is built for

NPC Forge is a local app for one person on one PC. It can spend your GPU's time, write and delete files in its library,
and start ComfyUI jobs, so it keeps everyone else out:

- **It listens on `127.0.0.1` only** and needs no login there.
- **Web pages can't use it.** It refuses a request whose `Host` header is not a loopback name, a request from another
  site's `Origin`, and a write that is not JSON, so a page you visit cannot reach it through the browser.
- **A network address needs a password.** It refuses to listen beyond loopback unless `NPCGEN_AUTH_TOKEN` holds at least
  16 characters; it then asks for HTTP Basic authentication (user `npcforge`, the token as the password). Basic
  authentication does not encrypt anything: use it only on a network you trust, behind an HTTPS proxy, a VPN or an SSH
  tunnel, and never expose the port to the internet.

What it does not protect against:

- **Anything already running as you.** A program on your PC can call its API like the page does.
- **Local extensions.** A folder in `local/extensions/` is Python code that runs inside the server with its full rights.
  Only put code there that you wrote or trust.
- **The models.** ComfyUI, its custom nodes and the voice engines are separate programs with their own security; NPC Forge
  only calls them. Install them from sources you trust.
- **What the models make.** Pictures, voices and 3D models are made by the models you install. Check their licences
  (`NOTICE.md`, and **Licences of every model** in the app) and never clone a real person's voice without their permission.

## Updates and downloads

**Check for updates** and `update.bat` contact GitHub only when you start them. The check asks `api.github.com` for the
newest release; the request names NPC Forge's version and nothing about you or your library. `update.bat` (and **Update
now**, which runs it) takes files only from github.com over HTTPS, refuses a release whose `npcgen/__init__.py` does not
match its tag, and keeps a copy of everything it replaces. Setup.exe installs for your Windows account alone and asks for no
administrator rights. Releases are not signed, so an update trusts this GitHub repository just as the first download did.

From 1.1.0 each release carries `FelsNPCForge-Setup-<version>.exe`, `FelsNPCForge-<version>.zip` and `SHA256SUMS`, which the
release workflow builds from the release's tag, with a
[GitHub attestation](https://docs.github.com/actions/security-for-github-actions/using-artifact-attestations) that records
which workflow run built them. In PowerShell, in the folder you downloaded them to:

```powershell
Get-FileHash .\FelsNPCForge-Setup-*.exe -Algorithm SHA256
Get-Content .\SHA256SUMS
```

The two hashes must be the same. With the [GitHub CLI](https://cli.github.com/),
`gh attestation verify FelsNPCForge-Setup-<version>.exe --repo Fel101/fels-npc-forge`, with the file's own name, checks that
this repository's release workflow built the file. Setup.exe is not signed, so Windows SmartScreen warns before it runs; the
hash and the attestation are how to check it. The workflow makes the same checks before anyone can download the files: it
makes each release as a draft, downloads its files on a computer that did not build them, checks the hashes and
attestations, and installs that Setup.exe over the release before it. Only then is the release published.
