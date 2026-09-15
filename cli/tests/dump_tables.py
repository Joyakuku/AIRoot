"""Dev helper: print the frozen reason-code and invariant tables as Markdown."""

from __future__ import annotations

import collections

from airoot.caps.doctor import INVARIANTS
from airoot.exits import EXIT_MEANINGS, REASON_EXIT

grouped: dict[int, list[str]] = collections.defaultdict(list)
for code, value in sorted(REASON_EXIT.items()):
    grouped[value].append(code)

for value in sorted(grouped):
    cells = ", ".join("`" + code + "`" for code in grouped[value])
    print(f"| {value} | {EXIT_MEANINGS[value]} | {cells} |")

print()
for key in sorted(INVARIANTS, key=lambda name: int(name[1:])):
    cells = ", ".join("`" + code + "`" for code in INVARIANTS[key])
    print(f"| {key} | {cells} |")
