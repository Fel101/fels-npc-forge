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
