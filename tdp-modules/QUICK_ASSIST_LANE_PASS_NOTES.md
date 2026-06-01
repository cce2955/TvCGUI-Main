Changed:
- assist_scanner_backend.py

What changed:
- Quick-assist button clicks no longer run route resolving / chr_tbl reads / memory writes on the main UI lane.
- apply_quick_assist_from_main() now validates the click, queues the assist intent, starts a dedicated quick-assist worker lane, and returns immediately.
- The worker lane serializes the real resolver/write work off the pygame HUD loop.
- Repeated clicks on the same slot are coalesced so only the latest requested assist for that slot is applied.
- A click also schedules route prewarm immediately, but the UI does not wait for that prewarm.
- Safe Restore / Hard Reset now clear pending quick-assist lane jobs too.
- Tool State debug data now exposes quick-assist lane pending count/order/last result.

Expected result:
- Main GUI quick-assist buttons should stop causing visible click-delay/stutter.
- Cold routes may still take time to apply, but that work happens on the quick-assist lane instead of freezing the main GUI.
- Console will show queued/applied/failed messages from the lane.
