# ZCE Build Log
### Full Development History — March 1, 2026

This document records every decision made, every bug encountered, and every fix applied during the development of the Zero-Count Encoding System. Written honestly, including all the dead ends.

---

## Part 1 — The Idea

### Where it started

The original intuition wasn't precise. It was something like: *zeros and ones should mean different things depending on where they are, and the pattern of zeros could tell you how to read what follows.* This idea had been floating around for months without a concrete form.

The first concrete version was: zeros as delimiters, like HTML tags. `00000000` opens something, the same sequence closes it. Similar to how `<p>` and `</p>` work but in binary.

### First reality check

This already exists. Magic bytes, file format headers, HDLC framing — most binary formats use zero-sequences as markers. The question became: is there something different here?

The potentially novel angle: **the count of zeros directly IS the meaning**, without pointing to a lookup table. Not "8 zeros = go check this table for what mode you're in." Just: 8 zeros = this specific thing, universally, by rule alone.

### The grammar/data split

The first structural decision: zeros as grammar tokens should never collide with data. The solution was obvious once stated — keep data completely separate. Grammar section first, data section after, divided by a separator. This removes any possibility of zeros in actual data values being misread as grammar.

---

## Part 2 — V1 Core Encoder/Decoder

### Design: the grammar token

Settled on: **N zeros followed by a terminating 1**. The `1` acts as a stop bit. This means:

- Tokens are variable length but unambiguous — you always know where one ends
- The terminating `1` makes consecutive tokens easy to parse
- The decoder is a single loop: count zeros, hit 1, act on count

Token assignments (v1):
```
N=1  value reference
N=2  instruction
N=3  unit open/close
N=4  block open/close
N=5  section open/close
N=7  document open/close
```

N=6 intentionally skipped at this stage, reserved.

### Design: unary pointers

When a value reference token (N=1) appears, the decoder needs to know which data slot to read. The natural choice was to encode this as a pointer.

First idea: use 8 raw bits as a pointer (0–255 data slots). This seemed simple.

**Bug 1: Raw pointer bits collide with grammar**

When encoding pointer `data[0]` as `00000000` (8 bits for index 0), and this appears right after a `N=1` token in the grammar stream, the decoder reads those 8 zeros as a new grammar token instead of pointer data. The grammar parser can't tell the difference.

The fix considered: read 8 bits raw, bypassing the zero-count parser for that step. Tried this — worked for simple cases but the terminating `1` accounting got messy.

**Real fix: unary pointers**

Encode index N as (N+1) ones followed by a zero: `1...10`. This encoding contains **no zero-runs at all** (only the final terminator zero, which immediately follows a run of ones). Since grammar tokens always start with zeros, a unary pointer starting with ones is immediately distinguishable.

- `data[0]` → `10`
- `data[1]` → `110`
- `data[5]` → `1111110`

This adds overhead for large indices (data[100] = 101 bits) but guarantees zero collision anywhere in the grammar stream.

### Design: the separator

First version used `00000000` (8 zeros) as a separator between grammar and data sections.

The justification: the maximum grammar token is N=7 (7 zeros + 1). Any run of 8 zeros could only be the separator, not a grammar token.

This seemed sound — and it was, for v1. Problem came later with jump tokens in v2.

### V1 implementation

Wrote encoder, decoder, and tests. Ran into the raw pointer bug described above. Fixed with unary pointers. Five tests passed:
- ADD 3+5=8
- SUB 10-4=6
- MUL 7×6=42
- ADD then MUL
- STORE 42

---

## Part 3 — V1 Compiler and VM

### Decision: what language to compile from

Didn't try to design a complex language. Just enough to test the bytecode: assignment, arithmetic, print. The simplest possible syntax.

```
x = 3 + 5
y = x * 2
print y
```

### Compiler architecture

Three parts:
1. **Line parser** — reads each line, identifies statement type
2. **Expression compiler** — splits on operators, allocates data slots
3. **Encoder** — wraps emitted instructions in grammar tokens

The data section is treated as a flat array. Every literal and every variable gets a slot. Arithmetic operations get an extra slot for the result. A LOAD instruction copies results into variable slots.

So `x = 3 + 5` compiles as:
```
data[0] = 3 (literal)
data[1] = 5 (literal)
data[2] = 0 (ADD result slot)
data[3] = 0 (variable x)
emit: ADD(data[0], data[1], data[2])
emit: LOAD(data[2], data[3])
```

This is wasteful — lots of data slots — but simple and correct.

### VM architecture

Simple PC-based loop. No registers, no stack. All state in the data array. Each instruction reads from and writes to data slots directly.

First version executed instructions sequentially. No jumps. This was enough for v1.

### V1 tests: all passed (4/4)

---

## Part 4 — V2: Adding Conditionals and Loops

### The design challenge: jumps

To support if/else and while, the VM needs to jump — skip instructions or go back. This requires:
1. A way to encode a jump in the grammar
2. A way to encode a jump target
3. A way for the compiler to know the target before it's compiled (forward patching)

### Token assignment for jumps

- N=6: jump-if-false (JIF) — takes condition data index + target instruction index
- N=9: unconditional jump (JMP)

These tokens don't use the N=2 instruction wrapper — they have their own grammar token IDs.

Jump targets are **instruction indices** not bit offsets. This was an explicit design decision: bit offsets would require knowing the exact binary size of everything after the jump point, which isn't known at compile time. Instruction indices are just integers the compiler can track easily.

### Decision: forward patching

When the compiler encounters `if x > 5:`, it doesn't yet know where to jump if the condition is false — it hasn't compiled the body yet.

Solution: emit JIF with target=0 (placeholder), compile the body, then go back and replace the 0 with the real target. This is called forward patching and is standard in compiler design.

The compiler tracks a `patch()` method that modifies a specific instruction's jump_to field after emission.

### Bug 2: Separator collision with N=9 (JMP) token

When v2 was first run, all tests with jumps (if/while) failed with:

```
AssertionError: Separator collision at grammar position
```

**Diagnosis:** The separator was `00000000` (8 zeros). The JMP token was N=9, which encodes as `000000001` (9 zeros + 1). When a JMP token followed another token (whose terminating `1` preceded it), the sequence became:

```
...1 [000000001] ...
```

Looking at this: the `1` from the previous token, then 9 zeros, then `1`. Within those 9 zeros there is a run of 8 consecutive zeros = collision with the separator pattern.

More specifically: `ztok(4) + ztok(3) + ztok(9)` produces `00001 0001 000000001`. Searching this string for `00000000` finds a match because `00001 0001 00000000 1` — the last 8 of the 9-zero token's zeros match.

**Attempted fix 1:** Change separator to `100000001` (1 + 7 zeros + 1).

This also failed. `ztok(7)` = `00000001`. When preceded by the terminating `1` of any token: `...1 00000001` = `100000001` = matches the new separator.

**Real fix: abandon pattern-based separation entirely.**

Instead of searching for a bit pattern to delimit grammar from data, store the grammar length explicitly as a 16-bit header at the start of the bitstream:

```
[ 16-bit grammar length ] [ grammar bits ] [ data bits ]
```

The decoder reads 16 bits, gets the grammar length, reads exactly that many bits as grammar, and treats everything after as data. Zero possibility of collision. No pattern matching needed.

This is also cleaner conceptually — the format is self-describing about its own structure from the very first bits.

Side effect: changed data values from 8-bit to 16-bit at the same time. This fixed the limitation of values being capped at 255 and allowed numbers up to 65535.

### Bug 3: While loop condition re-evaluation

First while loop test failed — the loop ran once and stopped.

**Diagnosis:** The compiler was evaluating the condition (e.g. `i < 5`) once before the loop started, emitting a CMP instruction, then emitting JIF based on that. Inside the loop body, `i` was being modified, but the condition wasn't being re-checked — the JMP at the end jumped back past the condition.

**Fix:** The loop_top pointer was set to point at the CMP instruction itself, not before it. The JMP at the end of the body jumps back to the CMP instruction index, forcing condition re-evaluation on every iteration.

```
2: CMP_LTE i, lit(10) → cond     ← loop_top = 2
3: JIF cond → [after loop]
4: ... body ...
5: JMP → 2                         ← back to CMP
```

### Bug 4: Block indentation detection

The `_find_block` method that extracts indented sub-blocks had an off-by-one issue — it was including the `else:` line itself in the then-block, causing double-compilation.

**Fix:** The block finder stops when it encounters a line whose indentation is less than or equal to the parent block's indentation. The `else:` line sits at the same indentation as `if`, so it correctly stops the then-block and is handled separately by `_compile_if`.

### V2 tests

First run after header fix: 5/18 passed (arithmetic only). Jumps all failing due to separator collision — the fix hadn't been applied yet.

After separator → header fix: tested arithmetic first (still 5/5), then if/else. If/else passed. While loops failed with the condition re-evaluation bug.

After condition re-eval fix: 20/23 passing.

After indentation fix: 23/23 passing.

---

## Part 5 — Final Test Suite

23 tests covering:

**Arithmetic:** basic, chained, multi-variable, division, 16-bit numbers

**Conditionals:** if true, if false, if-else true, if-else false, equality, not-equal

**Loops:** count up, sum 1-10, countdown

**Complex programs:** factorial 5, fibonacci 10th, max of two numbers, absolute difference, if-inside-while, nested arithmetic, power of 2

All 23 passing. No regressions.

---

## Summary of All Bugs

| # | Bug | Where | Root Cause | Fix |
|---|-----|-------|-----------|-----|
| 1 | Raw pointer bits read as grammar tokens | V1 encoder/decoder | `00000000` (index 0) looks like 8-zero grammar token | Switched to unary pointers (ones-terminated-by-zero) |
| 2 | Separator collision with JMP token | V2 encoder | N=9 token has 9 zeros; run of 8 within it matches `00000000` separator | Replaced pattern separator with 16-bit length header |
| 3 | While loop runs once | V2 compiler | JMP jumped past condition re-evaluation | Set loop_top to CMP instruction index, not loop start |
| 4 | Else block double-compiled | V2 compiler | Block finder included `else:` line in then-block | Stop block finder when indent ≤ parent indent |

---

## Key Design Decisions Summary

| Decision | Options Considered | Choice Made | Reason |
|----------|--------------------|-------------|--------|
| Pointer encoding | Raw bits, fixed-width, unary | Unary (ones+zero) | Zero-free — can't collide with grammar tokens |
| Grammar/data separation | Pattern separator, length header | Length header | Pattern separators are vulnerable to collision; header is structurally clean |
| Jump targets | Bit offsets, instruction indices | Instruction indices | Simpler for compiler; bit offsets require knowing binary sizes ahead of time |
| Data width | 8-bit, 16-bit, 32-bit | 16-bit | 8-bit too limiting (max 255); 32-bit adds overhead; 16-bit practical for current scope |
| Jump instruction encoding | Reuse N=2 wrapper, own token | Own token (N=6, N=8) | Jumps don't fit the standard arg pattern; cleaner as dedicated tokens |
| Condition handling | In-place boolean, separate slot | Separate data slot | Clean — condition result is a regular data value; VM just reads it |

---

*Build log written March 1, 2026*