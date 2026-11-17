# Security policy

## Reporting a vulnerability

Report vulnerabilities **privately** with GitHub's private vulnerability
reporting: open the affected repository → **Security** → **Report a
vulnerability**. Do not open a public issue.

Include the affected component and version, the impact, and steps to reproduce.
We aim to acknowledge reports within 7 days. We will agree a disclosure date with
you, by default no more than 90 days after the report.

## Supported versions

Only the latest release of each repository gets security fixes.

## Scope notes specific to this project

**harmony-server answers for Logitech hostnames on your LAN.** To serve hubs,
it is reached as `*.myharmony.com` through local DNS overrides, and it may
generate its own certificate authority for those names. Treat that as
security-sensitive:

- Keep generated CA keys and server keys private and on the server. Never commit
  them, and never put them in a Docker image or a release.
- Never install the server's CA on general-purpose machines beyond what the
  setup documentation asks for.
- Expose harmony-server only on your LAN. It is not designed to face the internet.

**There are no Logitech TLS certificates or private keys here to abuse.**
Harmony Revival holds no Logitech signing keys, certificates or credentials, and
none will be accepted. If you find any Logitech private key, credential or
customer data in any Harmony Revival repository, issue, release or image, report
it privately as above so it can be removed and the history rewritten.

**Packet captures contain secrets.** Hub captures can include Logitech account
passwords, session tokens and account IDs. If you see an unredacted capture in a
public issue or discussion, report it with "Report content", or privately as
above, rather than replying to it.

## Out of scope

- Vulnerabilities in Logitech's own services, firmware or apps. Report those to
  Logitech.
- Harmony Hub firmware weaknesses that the project documents for
  interoperability, such as the plaintext LAN APIs on ports 8088 and 5222.
