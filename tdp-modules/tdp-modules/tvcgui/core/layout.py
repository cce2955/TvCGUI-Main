# layout.py
#
# HUD layout + giant-handling logic extracted from main.py

import pygame


def compute_layout(w, h, snaps):
    """
    Compute all main HUD rects (panels, activity bar, events, debug, scan).
    Adjusts layout when giants are detected (they have no partners).
    """
    pad = 10
    gap_x = 20
    gap_y = 10

    panel_w = (w - pad * 2 - gap_x) // 2
    panel_h = 155

    row1_y = pad
    row2_y = row1_y + panel_h + gap_y

    # Check for giants (IDs 11 = Gold Lightan, 22 = PTX-40A)
    GIANT_IDS = {11, 22}
    p1_is_giant = snaps.get("P1-C1", {}).get("id") in GIANT_IDS
    p2_is_giant = snaps.get("P2-C1", {}).get("id") in GIANT_IDS

    # Default positions
    r_p1c1 = pygame.Rect(pad, row1_y, panel_w, panel_h)
    r_p2c1 = pygame.Rect(pad + panel_w + gap_x, row1_y, panel_w, panel_h)
    r_p1c2 = pygame.Rect(pad, row2_y, panel_w, panel_h)
    r_p2c2 = pygame.Rect(pad + panel_w + gap_x, row2_y, panel_w, panel_h)

    # If P1 is a giant, move P2-C1 down to row 2 (where P2-C2 would be)
    if p1_is_giant:
        r_p2c1 = pygame.Rect(pad + panel_w + gap_x, row2_y, panel_w, panel_h)
        # P1-C2 stays in row 2 left but will be hidden/empty

    # If P2 is a giant, P2-C1 stays top right, P2-C2 hidden/empty
    # (layout rectangles themselves don't change further)

    act_rect = pygame.Rect(pad, r_p1c2.bottom + 30, w - pad * 2, 32)

    events_y = act_rect.bottom + 8
    events_h = 150

    # split row into left (events) and right (debug) halves
    row_w = w - pad * 2
    half_w = (row_w - gap_x) // 2

    events_rect = pygame.Rect(pad, events_y, half_w, events_h)
    debug_rect = pygame.Rect(pad + half_w + gap_x, events_y, half_w, events_h)

    scan_y = events_rect.bottom + 8
    scan_h = max(90, h - scan_y - pad)
    scan_rect = pygame.Rect(pad, scan_y, w - pad * 2, scan_h)

    return {
        "p1c1": r_p1c1,
        "p2c1": r_p2c1,
        "p1c2": r_p1c2,
        "p2c2": r_p2c2,
        "act": act_rect,
        "events": events_rect,
        "debug": debug_rect,
        "scan": scan_rect,
        "p1_is_giant": p1_is_giant,
        "p2_is_giant": p2_is_giant,
    }


def reassign_slots_for_giants(snaps):
    """
    When giants are present, the game loads 3 character tables instead of 4.
    This function reassigns them to the correct logical slots.
    
    Rules:
    - If P1-C1 is giant (11/22): other 2 chars go to P2-C1, P2-C2
    - If P2-C1 is giant (11/22): other 2 chars go to P1-C1, P1-C2
    - If both P1-C1 and P2-C1 are giants: just P1-C1, P2-C1
    """
    if not snaps:
        # Always return *something* dictionary-like
        return snaps

    GIANT_IDS = {11, 22}
    
    # Get current slot assignments
    p1c1 = snaps.get("P1-C1")
    p1c2 = snaps.get("P1-C2")
    p2c1 = snaps.get("P2-C1")
    p2c2 = snaps.get("P2-C2")
    
    # Check which slots have giants
    p1c1_is_giant = p1c1 and p1c1.get("id") in GIANT_IDS
    p2c1_is_giant = p2c1 and p2c1.get("id") in GIANT_IDS
    
    # Case 1: Both are giants - no reassignment needed, just remove partners
    if p1c1_is_giant and p2c1_is_giant:
        snaps.pop("P1-C2", None)
        snaps.pop("P2-C2", None)
        return snaps
    
    # Case 2: P1 is giant, P2 has a team
    if p1c1_is_giant:
        # P1-C2 should be empty, other slots should be P2 team
        reassigned = {
            "P1-C1": p1c1,
        }
        
        # The other 2 non-giant characters go to P2-C1 and P2-C2
        non_giant_chars = []
        if p1c2 and p1c2.get("id") not in GIANT_IDS:
            non_giant_chars.append(p1c2)
        if p2c1 and p2c1.get("id") not in GIANT_IDS:
            non_giant_chars.append(p2c1)
        if p2c2 and p2c2.get("id") not in GIANT_IDS:
            non_giant_chars.append(p2c2)
        
        # Assign them to P2 slots
        if len(non_giant_chars) >= 1:
            reassigned["P2-C1"] = non_giant_chars[0]
            reassigned["P2-C1"]["slotname"] = "P2-C1"
        if len(non_giant_chars) >= 2:
            reassigned["P2-C2"] = non_giant_chars[1]
            reassigned["P2-C2"]["slotname"] = "P2-C2"
        
        return reassigned
    
    # Case 3: P2 is giant, P1 has a team
    if p2c1_is_giant:
        # P2-C2 should be empty, other slots should be P1 team
        reassigned = {
            "P2-C1": p2c1,
        }
        
        # The other 2 non-giant characters go to P1-C1 and P1-C2
        non_giant_chars = []
        if p1c1 and p1c1.get("id") not in GIANT_IDS:
            non_giant_chars.append(p1c1)
        if p1c2 and p1c2.get("id") not in GIANT_IDS:
            non_giant_chars.append(p1c2)
        if p2c2 and p2c2.get("id") not in GIANT_IDS:
            non_giant_chars.append(p2c2)
        
        # Assign them to P1 slots
        if len(non_giant_chars) >= 1:
            reassigned["P1-C1"] = non_giant_chars[0]
            reassigned["P1-C1"]["slotname"] = "P1-C1"
        if len(non_giant_chars) >= 2:
            reassigned["P1-C2"] = non_giant_chars[1]
            reassigned["P1-C2"]["slotname"] = "P1-C2"
        
        return reassigned
    
    # Case 4: No giants - return as-is
    return snaps
