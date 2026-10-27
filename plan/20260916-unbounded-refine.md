# Unbounded Refine generation

User requests removal of the 30-minute total limit. Continue on the existing authorized feat/isabelle-proof-backend branch. Preserve historical run bindings.

- Support explicit null total, iteration and iteration-count limits, with finite checkpoint calls.
- Make seL4 deployment default to unbounded persistent generation; normal agent replies do not imply completion.
- Require independently checked positive contracts and successful repository integration for completion.
- Preserve explicit bounded configurations, errors and manual process interruption as non-completion.
- Test checkpoint continuation past 1800 seconds with normal agent returns and checker-gated completion; document operational semantics. Do not start a paid experiment as part of this configuration change.
