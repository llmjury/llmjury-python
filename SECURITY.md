# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | ✅        |

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Report vulnerabilities by email to **security@llmjury.com**. Include:

- A description of the issue and its impact
- Steps to reproduce (a proof of concept helps)
- Affected SDK version(s)

You will receive an acknowledgement within **2 business days** and a status
update within **7 days**. We ask that you give us a reasonable window to ship
a fix before public disclosure; we will credit reporters in the release notes
unless you prefer otherwise.

## Scope notes

- This SDK is designed to be used with your organization's **publishable** API
  key (`llmj_pk_...`), which is write-only and rate-limited. Never embed a
  secret key in an application that ships to users.
- The SDK never logs API keys, prompts, or payload contents at default log
  levels, and never throws into the host application.
