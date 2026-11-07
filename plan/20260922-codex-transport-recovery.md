# Recover the independent Codex experiment's communication

Continue on the previously approved feat/codex-autonomous-baseline branch. Preserve checker implementation, source snapshots, accepted certificates, all contract counters and the current Codex thread.

Use a run-specific executable transport wrapper outside the frozen checker implementation. Explicitly select Responses SSE with a 180000 ms stream idle timeout and bounded native request/stream retries. After a transport failure, resume the exact same thread. Add an outer guard for silent transport failures only when no tool is active; never apply a 180-second total runtime limit or interrupt native proof checking for ordinary silence. Test command construction and failure/recovery behavior with fake CLI streams before deployment.

Stop the stalled generator at a safe boundary, archive old state/config/binding and logs, and migrate only the agent executable in the run binding with explicit evidence. Keep controller identity and ledger byte-for-byte unchanged. Deploy the wrapper privately within this worker, restart supervision and verify resumed thread ID, configured Astra medium, checker reachability, unchanged starting counters and new proof activity.
