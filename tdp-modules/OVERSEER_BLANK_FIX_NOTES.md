# Overseer blank window fix

This patch fixes a blank Overseer body by making the body render before the first runtime snapshot pull.

Changes:

- The text body now inserts a visible `Overseer loading runtime state...` message immediately.
- The first refresh is scheduled with `after(50, ...)` instead of running synchronously during window construction.
- Refresh errors are now written into the text body instead of only the footer status label.
- The main overseer state snapshot now protects assist/perf/active quick-assist copies from transient dictionary mutation errors.

This does not change gameplay behavior. It only makes the Overseer panel visible and debuggable if state collection fails.
