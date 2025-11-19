def find_control_pattern_around(base: int,
                                forward_window: int = 0x80,
                                radius_back: int = 0x40):
    """
    More robust control-pattern finder:

    1) Try the original behavior: scan base..base+forward_window.
    2) If nothing is found, scan a symmetric window around base:
         [base - radius_back, base + radius_back]

    Works both when 'base' is the true start of the control struct
    (scan_normals_all ABS) and when 'base' is inside a house block
    (your hitbox addresses).
    """
    # Pass 1: original behavior
    id_off, id0 = find_control_pattern(base, window_size=forward_window)
    if id_off is not None:
        return id_off, id0

    # Pass 2: scan around the address
    start = base - radius_back
    if start < 0:
        start = 0
    size = radius_back * 2

    data = rbytes(start, size) or b""
    n = len(data)
    if n < 4:
        return None, None

    # strict grounded pattern
    if n >= 8:
        for i in range(n - 7):
            if (
                data[i]     == 0x00 and
                data[i + 1] == 0x00 and
                data[i + 2] == 0x00 and
                data[i + 3] == 0x00 and
                data[i + 4] == 0x01 and
                data[i + 6] == 0x01 and
                data[i + 7] == 0x3C
            ):
                id0 = data[i + 5]
                if 0x00 <= id0 <= 0xBE:
                    # return offset relative to the *true* base (your address)
                    id_addr = start + i + 5
                    return id_addr - base, id0

    # looser air / variants
    for i in range(n - 3):
        if data[i] == 0x01 and data[i + 2] == 0x01 and data[i + 3] in (0x3C, 0x3F):
            id0 = data[i + 1]
            if 0x00 <= id0 <= 0xBE:
                id_addr = start + i + 1
                return id_addr - base, id0

    return None, None
