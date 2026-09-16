# Isabelle Refine trial

Benchmark: `a1f1f6dd7d9fbf927d700e65ae21d043cf5d1e29408c22ef36c78f7acfc617c0`.

Run status: `budget_exhausted`.

## Iteration 0

Positive proofs: **35/287**. Negative proofs: 0. Entire task complete: False.

Agent wall time: 1800.01s. Independent evaluation: 200.05s. Model: `gpt-6-astra`. Agent OK: False. Timeout: True.

Reported usage: `{}`. Missing usage is unknown, not zero. Dollar cost is unknown unless explicitly reported.

| Module | Contracts | Proved | Disproved | Other statuses |
| --- | ---: | ---: | ---: | --- |
| Vero_ActiveIrq | 4 | 4 | 0 | none |
| Vero_ArchDecode | 1 | 0 | 0 | unfilled: 1 |
| Vero_ArchExecute | 1 | 0 | 0 | unfilled: 1 |
| Vero_AsidPoolExecute | 2 | 0 | 0 | unfilled: 2 |
| Vero_AsidPoolObjects | 4 | 0 | 0 | unfilled: 4 |
| Vero_CNodeDecode | 1 | 0 | 0 | unfilled: 1 |
| Vero_CNodeExecute | 1 | 0 | 0 | unfilled: 1 |
| Vero_CapDerivation | 4 | 1 | 0 | unfilled: 3 |
| Vero_CapInsertion | 4 | 0 | 0 | unfilled: 4 |
| Vero_CapLookup | 6 | 0 | 0 | unfilled: 6 |
| Vero_CapMovement | 3 | 0 | 0 | unfilled: 3 |
| Vero_CapReads | 4 | 0 | 0 | unfilled: 4 |
| Vero_CheckedCapInsertion | 3 | 0 | 0 | unfilled: 3 |
| Vero_CorresCombinators | 6 | 6 | 0 | none |
| Vero_Detype | 1 | 0 | 0 | unfilled: 1 |
| Vero_EndpointProtocol | 3 | 0 | 0 | unfilled: 3 |
| Vero_EventDispatch | 1 | 0 | 0 | unfilled: 1 |
| Vero_FastDelete | 2 | 0 | 0 | unfilled: 2 |
| Vero_FaultDelivery | 3 | 0 | 0 | unfilled: 3 |
| Vero_FaultMessages | 6 | 0 | 0 | unfilled: 6 |
| Vero_FinaliseSlot | 7 | 0 | 0 | build_error: 3, unfilled: 4 |
| Vero_GeneralFinalise | 2 | 0 | 0 | unfilled: 2 |
| Vero_HardwareAsids | 12 | 1 | 0 | unfilled: 11 |
| Vero_HeapObjects | 5 | 0 | 0 | unfilled: 5 |
| Vero_InterruptDelivery | 1 | 0 | 0 | unfilled: 1 |
| Vero_InvocationDecode | 2 | 0 | 0 | unfilled: 2 |
| Vero_InvocationExecute | 1 | 0 | 0 | unfilled: 1 |
| Vero_IpcCancellation | 5 | 0 | 0 | unfilled: 5 |
| Vero_IpcCapTransfer | 4 | 0 | 0 | unfilled: 4 |
| Vero_IpcMessages | 4 | 0 | 0 | unfilled: 4 |
| Vero_IrqDecode | 3 | 0 | 0 | unfilled: 3 |
| Vero_IrqExecute | 3 | 0 | 0 | unfilled: 3 |
| Vero_IrqPrimitives | 8 | 7 | 0 | unfilled: 1 |
| Vero_KernelComposition | 3 | 0 | 0 | unfilled: 3 |
| Vero_MappingEntries | 10 | 1 | 0 | unfilled: 9 |
| Vero_MessageBuffers | 6 | 0 | 0 | unfilled: 6 |
| Vero_NotificationBindings | 3 | 0 | 0 | unfilled: 3 |
| Vero_NotificationProtocol | 3 | 0 | 0 | unfilled: 3 |
| Vero_PageTableObjects | 16 | 0 | 0 | unfilled: 16 |
| Vero_ReadyQueues | 10 | 2 | 0 | unfilled: 8 |
| Vero_RecursiveDelete | 2 | 0 | 0 | unfilled: 2 |
| Vero_ReplyTransfer | 2 | 0 | 0 | unfilled: 2 |
| Vero_RetypeObjects | 3 | 1 | 0 | unfilled: 2 |
| Vero_Scheduler | 4 | 0 | 0 | unfilled: 4 |
| Vero_SchedulerSwitch | 6 | 0 | 0 | unfilled: 6 |
| Vero_StateFields | 11 | 10 | 0 | unfilled: 1 |
| Vero_SyscallInvocation | 3 | 0 | 0 | unfilled: 3 |
| Vero_SyscallReceive | 3 | 0 | 0 | unfilled: 3 |
| Vero_SyscallReply | 2 | 0 | 0 | unfilled: 2 |
| Vero_SyscallYield | 1 | 0 | 0 | unfilled: 1 |
| Vero_TcbDecode | 13 | 1 | 0 | unfilled: 12 |
| Vero_TcbExecute | 1 | 0 | 0 | unfilled: 1 |
| Vero_TcbRegisterExecute | 3 | 0 | 0 | unfilled: 3 |
| Vero_TcbRootReplace | 1 | 0 | 0 | unfilled: 1 |
| Vero_TcbValidation | 3 | 0 | 0 | unfilled: 3 |
| Vero_ThreadActivation | 2 | 0 | 0 | unfilled: 2 |
| Vero_ThreadLifecycle | 4 | 0 | 0 | unfilled: 4 |
| Vero_ThreadObjects | 13 | 0 | 0 | unfilled: 13 |
| Vero_ThreadState | 1 | 0 | 0 | unfilled: 1 |
| Vero_TimerTick | 1 | 0 | 0 | unfilled: 1 |
| Vero_UntypedDecode | 1 | 0 | 0 | unfilled: 1 |
| Vero_UntypedExecute | 1 | 0 | 0 | unfilled: 1 |
| Vero_UntypedIndices | 4 | 0 | 0 | unfilled: 4 |
| Vero_UntypedReset | 1 | 0 | 0 | unfilled: 1 |
| Vero_UserExecution | 3 | 0 | 0 | unfilled: 3 |
| Vero_UserMemory | 4 | 1 | 0 | unfilled: 3 |
| Vero_UserRegisters | 11 | 0 | 0 | unfilled: 11 |
| Vero_VMRoots | 2 | 0 | 0 | unfilled: 2 |
| Vero_VSpaceCleanup | 6 | 0 | 0 | unfilled: 6 |
| Vero_VSpaceExecute | 3 | 0 | 0 | unfilled: 3 |
| Vero_VSpaceLookup | 4 | 0 | 0 | unfilled: 4 |

Shell telemetry: `{"completed": 95, "succeeded": 62, "failed": 33, "shell_commands_mentioning_check_tool": 45}`.

CLI errors: `[]`.

This is a bounded pilot on one model and one input snapshot. It is not a statistical comparison with another system. Negative proofs do not count as Refine completion. Module errors can invalidate otherwise completed slots within that module.
