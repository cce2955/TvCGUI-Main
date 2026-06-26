# Character-select stability fix

Disabled mode restores the native 27-slot roster only when the exact extended Extra Characters signature remains resident.

The restore path writes the native slot IDs back to slots `0x00` through `0x1A`, clears appended slots only, and restores the stock count `0x1B`. It does not zero the native roster.

No background roster write loop runs while Extra Characters and Solo Team are disabled.
