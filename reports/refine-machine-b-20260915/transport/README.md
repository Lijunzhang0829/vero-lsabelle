# Transport used by this pilot

These are the exact temporary transport scripts used in this pilot, archived for reproduction and diagnosis. They are experiment-specific, not a general deployment service. They contain no authentication credentials.

- `local-tunnel.py.txt` runs on the primary host, where the existing HTTP proxy listens at `127.0.0.1:7890`. Each SSH connection requests a newly allocated remote loopback port and writes it to machine B's run control file. Explicit `ControlPersist=no` prevents the machine's global four-hour persistence setting from detaching the monitored SSH process. Keep the script in the foreground and terminate it when finished; the outer two-hour limit is checked between connection attempts.
- `worker-relay.py.txt` runs as the `agent` user in the dedicated worker with host networking. It listens only on `127.0.0.1:18894`, reads `/control/proxy-port`, and forwards connections to the current SSH forwarding port. When that port changes, it closes old connections so the model client can reconnect instead of waiting on a stale SSH channel.

The model's process receives `http_proxy` and `https_proxy` pointing to port 18894. The prover uses the read-only local runtime and heaps. Stop the worker and the local tunnel after the experiment. The scripts neither alter the SSH server configuration nor terminate SSH daemon processes.
