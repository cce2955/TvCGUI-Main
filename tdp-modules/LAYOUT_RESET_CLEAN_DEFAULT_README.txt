Layout reset clean-default fix

Why the previous reset was cramped
- It restored whatever saved view state had been active at snapshot time.
- In this case that included the raw All-columns layout, which squeezed every scouting field into the table.

What changed
- Reset View now always returns to the clean Frame-data default:
  - Frame columns only
  - built-in sane column widths
  - filters cleared
  - profile/notation order restored
  - table horizontally reset to the left
  - right inspector pane restored to a readable width
- It keeps the current valid row density.
- It still does not alter move edits; Reset all remains the actual data reset.
