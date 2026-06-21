Final UI polish

1) More visual table sorting
- Clicking a column header now shows a compact ▲ / ▼ direction marker in that header.
- A live Sort: <Column> <arrow> badge appears in the top command bar.
- The status bar also reports the active sort direction.

2) Edited-row marker
- Rows changed in the current patch session now get a small ● marker in the tree gutter.
- The existing edited-row tint remains, so changed rows are identifiable both while scanning and when the row is selected.

3) Styled message boxes
- Added an app-styled modal notice/confirmation system and patched tkinter.messagebox once through fd_widgets.
- Existing showerror/showwarning/showinfo/askyesno/askokcancel/askquestion calls from the Frame Data workbench and its subwindows now use the same styled app modal rather than native Windows message boxes.
- Styled notices use the app titlebar icon logic, type-colored top rail, readable wrapped text, and app-consistent buttons.

Notes
- No new scanners, profile reads, or Dolphin reads were added.
- This pass is UI only.
