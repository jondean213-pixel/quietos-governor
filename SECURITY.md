# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | Yes       |
| < 0.1   | No        |

## Reporting a vulnerability

QuietOS is a runtime governor for AI agents — its whole job is holding
under attack, so security reports are welcome and taken seriously.

Please report vulnerabilities privately by email:

**jon@deanwaylabs.com**

Include what you sent the governor, what it did, and what you expected
it to do instead. A proof-of-concept against the public proof log's
attack categories is helpful but not required.

Please do not open a public issue for an unpatched vulnerability.

## What to expect

QuietOS is maintained by one person. Expect an acknowledgment within
a few days. Confirmed holes get fixed, retested, and written into the
public proof log (`docs/PROOF-LOG.md`) once a fix is available — that
log, holes included, is part of the product.

## Scope

In scope: the governor's tier classification, approval binding and
expiry, audit-log integrity, secret redaction, and any path that lets
a brain execute an action the governor should have held or blocked.

Out of scope: the underlying model or brain you connect to QuietOS,
and the host environment it runs on.
