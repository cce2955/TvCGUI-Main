# GUI responsiveness hardening

This patch fixes two systemic Tk-host problems that could make an already-open
window stop accepting input after a background scan or runtime update:

1. `tk_host.py` previously drained its cross-thread queue until it was empty.
   A fast producer could keep the queue non-empty indefinitely, starving the
   Windows paint/input loop.
2. Several worker threads called `Toplevel.after(0, ...)` directly.  That is a
   Tcl call from the wrong thread.  It is intermittent by nature and can hang
   or flood a window while it is being closed or updated.

Changes:

- The host queue now processes a short bounded slice (24 tasks / about 6 ms),
  then returns control to Tk. Pending work resumes one millisecond later.
- High-frequency status/progress updates are coalesced: only the latest value
  is retained instead of building an unbounded callback backlog.
- Frame Data, Unknown Static Signals, Assist Scanner, and Projectile Scanner
  now return worker results through `tk_call_widget()` rather than calling
  `widget.after()` from worker threads.
- A watchdog writes an automatic Python-stack snapshot if the Tk host stops
  beating for four seconds.

Diagnostics are written only if needed:

`%TEMP%\tvc_tk_stall.log`

The log includes slow Tk callbacks and full thread stacks during a real stall.
It is safe to send that text back for the next pass.
