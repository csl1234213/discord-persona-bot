# Security reporting

This code is under pre-release security review. Do not deploy the development tree
or publish its history. No release is approved yet.

Report vulnerabilities privately to the repository owner using a contact channel
you already trust. Do not include tokens, private conversations, database exports,
recordings or raw scanner reports in public issues. A permanent private reporting
address must be supplied by the owner before release.

Rotate compromised credentials with their issuer. Removing a file or rewriting
Git history does not revoke a credential. Never test a suspected leaked credential.

Only an allowlisted staging archive is a potential distribution input.
`compose.release.yml` enables evidence gating and disables voice by default.
Development mode is not the release security profile.
