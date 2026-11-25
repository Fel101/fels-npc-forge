# Network security

The server binds to `127.0.0.1` only and refuses cross-origin, wrong-host and non-JSON requests, because it can use your GPU
and delete files. Loopback needs no login.

Binding to a LAN or other non-loopback address is refused unless `NPCGEN_AUTH_TOKEN` contains at least 16 ASCII characters.
When it is set, NPC Forge uses HTTP Basic authentication with username `npcforge` and the token as the password:

```powershell
$env:NPCGEN_AUTH_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
python server.py --host 0.0.0.0
```

Save the generated token in a password manager; you will need it as the browser password. Basic authentication does not
encrypt traffic. Use this only on a trusted private network, and preferably behind an HTTPS reverse proxy, VPN, or SSH
tunnel. Never expose the port directly to the public internet.

To report a security problem privately, see [SECURITY.md](../SECURITY.md).
