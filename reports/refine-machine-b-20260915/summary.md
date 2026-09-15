# Isabelle Refine trial

Benchmark: `4a9653c8e4b28ccd9a68e0c9f2b35cd1a6a6d3fe0b664670bdb6ecdd44f18be8`.

Run status: `budget_exhausted`.

## Iteration 0

Positive proofs: **39/287**. Negative proofs: 0. Entire task complete: False.

Agent wall time: 1800.01s. Independent evaluation: 178.66s. Model: `gpt-6-astra`. Agent OK: False. Timeout: True.

Reported usage: `{}`. Missing usage is unknown, not zero. Dollar cost is unknown unless explicitly reported.

| Module | Contracts | Proved | Disproved | Other statuses |
| --- | ---: | ---: | ---: | --- |
| Vero_Refine_ArchAcc_R | 24 | 1 | 0 | unfilled: 23 |
| Vero_Refine_Arch_R | 6 | 1 | 0 | unfilled: 5 |
| Vero_Refine_Bits_R | 2 | 2 | 0 | none |
| Vero_Refine_CNodeInv_R | 7 | 3 | 0 | unfilled: 4 |
| Vero_Refine_CSpace1_R | 12 | 0 | 0 | unfilled: 12 |
| Vero_Refine_CSpace_R | 8 | 0 | 0 | unfilled: 8 |
| Vero_Refine_Detype_R | 1 | 0 | 0 | unfilled: 1 |
| Vero_Refine_Finalise_R | 13 | 3 | 0 | unfilled: 10 |
| Vero_Refine_InterruptAcc_R | 3 | 2 | 0 | unfilled: 1 |
| Vero_Refine_Interrupt_R | 14 | 6 | 0 | unfilled: 8 |
| Vero_Refine_IpcCancel_R | 5 | 0 | 0 | unfilled: 5 |
| Vero_Refine_Ipc_R | 31 | 1 | 0 | unfilled: 30 |
| Vero_Refine_KHeap_R | 5 | 0 | 0 | build_error: 2, unfilled: 3 |
| Vero_Refine_Refine | 10 | 4 | 0 | unfilled: 6 |
| Vero_Refine_Retype_R | 4 | 1 | 0 | unfilled: 3 |
| Vero_Refine_Schedule_R | 20 | 3 | 0 | unfilled: 17 |
| Vero_Refine_Syscall_R | 16 | 1 | 0 | unfilled: 15 |
| Vero_Refine_TcbAcc_R | 34 | 7 | 0 | unfilled: 27 |
| Vero_Refine_Tcb_R | 33 | 2 | 0 | unfilled: 31 |
| Vero_Refine_Untyped_R | 6 | 0 | 0 | unfilled: 6 |
| Vero_Refine_VSpace_R | 33 | 2 | 0 | unfilled: 31 |

Shell telemetry: `{"completed": 217, "succeeded": 177, "shell_commands_mentioning_check_tool": 61, "failed": 40}`.

CLI errors: `[]`.

This is a bounded pilot on one model and one input snapshot. It is not a statistical comparison with another system. Negative proofs do not count as Refine completion. Module errors can invalidate otherwise completed slots within that module.
