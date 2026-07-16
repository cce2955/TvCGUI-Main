# layout.py
#
# HUD layout + giant-handling logic extracted from main.py

import pygame


def compute_layout(w, h, snaps):
    """Compute a responsive broadcast layout for the main window.

    The fighter cards retain enough room for portraits and quick assists, while
    the active lower workspace receives a real minimum height. This prevents
    the Normals Preview rows from being compressed into unreadable strips.
    """
    w = max(560, int(w))
    h = max(360, int(h))
    pad = 8
    gap_x = 18
    gap_y = 8

    panel_w = max(230, (w - pad * 2 - gap_x) // 2)

    # Reserve roughly the lower 43 percent for the tabbed workspace. The cap
    # keeps tall monitors from making the fighter cards unnecessarily small.
    desired_workspace_h = max(330, min(390, int(h * 0.53)))
    fixed_vertical = pad * 2 + gap_y + 14
    panel_h = (h - desired_workspace_h - fixed_vertical) // 2
    panel_h = max(126, min(158, panel_h))

    row1_y = pad
    row2_y = row1_y + panel_h + gap_y

    GIANT_IDS = {11, 22}
    p1_is_giant = snaps.get("P1-C1", {}).get("id") in GIANT_IDS
    p2_is_giant = snaps.get("P2-C1", {}).get("id") in GIANT_IDS

    r_p1c1 = pygame.Rect(pad, row1_y, panel_w, panel_h)
    r_p2c1 = pygame.Rect(pad + panel_w + gap_x, row1_y, panel_w, panel_h)
    r_p1c2 = pygame.Rect(pad, row2_y, panel_w, panel_h)
    r_p2c2 = pygame.Rect(pad + panel_w + gap_x, row2_y, panel_w, panel_h)

    if p1_is_giant:
        r_p2c1 = pygame.Rect(pad + panel_w + gap_x, row2_y, panel_w, panel_h)

    workspace_y = row2_y + panel_h + 14
    workspace_h = max(90, h - workspace_y - pad)
    workspace_rect = pygame.Rect(0, workspace_y, w, workspace_h)

    return {
        "p1c1": r_p1c1,
        "p2c1": r_p2c1,
        "p1c2": r_p1c2,
        "p2c2": r_p2c2,
        "act": workspace_rect.copy(),
        "events": workspace_rect.copy(),
        "debug": workspace_rect.copy(),
        "scan": workspace_rect.copy(),
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
