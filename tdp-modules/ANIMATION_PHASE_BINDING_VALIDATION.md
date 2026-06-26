# Phase Animation Binding Validation

Input dump: `tvc_memdump_20260624_104655.zip`

| Action root | Legacy command selected by prior resolver | Phase command selected by current resolver |
|---|---:|---:|
| `0x908AEEC8` | `0x908AF2BA` | `0x908AF2F2` |
| `0x909051D6` | `0x909051D6` | `0x9090520E` |

The phase command is six bytes after a `04 01 02 3F` record header. Both
selected phase commands retain `0x0101` in the captured dump, while the
previous direct targets show the prior writes.
