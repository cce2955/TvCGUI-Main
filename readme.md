# Tatsunoko vs Capcom HP Tracker

A Python script for tracking HP values in Tatsunoko vs Capcom using Dolphin Memory Engine.

## Installation

```bash
pip install dolphin-memory-engine
```

## Usage

```bash
python tvc_hp_poller_p1p2.py
```

The script will poll HP values for all 4 character slots (P1 Char1, P1 Char2, P2 Char1, P2 Char2) at 20Hz and display them in real-time.

## Configuration

- `POLL_HZ`: Polling frequency (default: 20Hz)
- `SHOW_SUPER`: Set to `True` to display super meter values

## Memory Structure Explained

### Why This Works

The memory structure is **direct pointer access**, not a multi-level pointer chain:

```
Base Address (0x803C9FCC) → Direct pointer to Character Struct
                            ├─ +0x24: Max HP
                            ├─ +0x28: Current HP  
                            ├─ +0x2C: Auxiliary HP (display/red HP)
                            └─ +0x4C: Super meter
```

### Critical Implementation Details

1. **Direct Pointer Reading**
   ```python
   # ✅ CORRECT:
   char_ptr = dme.read_word(PTR_P1_CHAR1)  # Read pointer at base address
   hp_current = dme.read_word(char_ptr + OFF_CUR_HP)  # Use pointer AS-IS
   
   # ❌ WRONG:
   char_ptr = dme.read_word(PTR_P1_CHAR1)
   final_addr = char_ptr + 0x9380  # DO NOT add extra offset!
   ```

2. **Correct HP Offsets**
   - `+0x24`: Max HP (NOT current HP)
   - `+0x28`: Current HP (NOT max HP)
   - `+0x2C`: Auxiliary HP value
   
   These are easily confused - make sure to read them correctly!

3. **Understanding Gecko Codes**
   
   The Gecko codes from lee4 can be misleading:
   ```
   48000000 803C9FD4    # Load pointer from 803C9FD4
   DE000000 90009380    # Gecko ASM instruction (NOT an offset!)
   14000024 0000B3B0    # Write 0xB3B0 to [pointer + 0x24]
   ```
   
   **Important**: The `DE000000 90009380` line is Gecko ASM syntax for conditional checks or base register operations, **NOT** an offset to add to the pointer value. This is the most common mistake when interpreting Gecko codes.

4. **Simple Validation**
   ```python
   if not char_ptr or (char_ptr & 0xFFF) == 0:
       return None  # Skip null or page-aligned (suspicious) pointers
   ```

### Common Mistakes to Avoid

**Adding extra offsets** - The pointer IS the character struct address  
**Swapping Max/Current HP offsets** - Max is at +0x24, Current is at +0x28  
**Reading as float** - HP values are 32-bit integers  
**Interpreting Gecko DE lines as offsets** - These are ASM instructions, not pointer arithmetic  
**Over-validating pointers** - Simple null check is sufficient  

### Memory Addresses

| Base Address | Description |
|--------------|-------------|
| `0x803C9FCC` | P1 Character 1 pointer |
| `0x803C9FDC` | P1 Character 2 pointer |
| `0x803C9FD4` | P2 Character 1 pointer |
| `0x803C9FE4` | P2 Character 2 pointer |

Each pointer points directly to a character struct with HP values at the offsets listed above.

## Output Format

```
P1-C1[90AB1234] 8500/10000 ( 85.0%) | P1-C2[90AB5678] 7200/9000 ( 80.0%) | ...
```

- **Address in brackets**: Memory location of character struct
- **HP ratio**: Current/Max HP
- **Percentage**: Current HP as percentage of max

## Credits

Based on Gecko codes by lee4. Memory structure analysis and implementation by the community.
