# ZCE Build Log
### Complete Development History

This document records every decision made, every bug encountered, and every fix applied across the entire development of the Zero-Count Encoding system. Written honestly, including all dead ends.

---

## Part 1 — The Idea (March 1, 2026)

### Origin

The original intuition: zeros and ones should mean different things, and the *count* of consecutive zeros could carry meaning without pointing to a lookup table. Not "8 zeros = go check this table." Just: N zeros = this specific thing, directly, by rule alone.

First concrete form: zeros as delimiters, like HTML tags. `00000000` opens something, same sequence closes it. This was immediately identified as already existing — magic bytes, HDLC framing, etc. The potentially novel angle: the count itself IS the meaning, without a table anywhere.

### First structural decision: grammar/data split

Zeros as grammar tokens must never collide with data. Solution: keep data completely separate. Grammar section first, data section after. Removes all possibility of zeros in data values being misread as grammar.

### Grammar token design

Settled on: **N zeros followed by a terminating 1**. The 1 acts as a stop bit. Tokens are variable-length but unambiguous — always know where one ends. The terminating 1 makes consecutive tokens parseable.

### Bug 1: Raw pointer bits read as grammar tokens

First pointer design: encode data slot index as 8 raw bits after a value-reference token. Immediately failed — `00000000` (index 0) in the grammar stream looks identical to 8 grammar zeros. Decoder reads pointer bits as a new grammar token.

**Fix:** Unary pointers. Encode index N as (N+1) ones followed by a zero: `1...10`. Contains no zero-runs, so can never be misread as a grammar token. The zero-run ambiguity problem is solved by encoding the pointer in a format that has no zero-runs.

---

## Part 2 — V1 Core (March 1, 2026)

Built encoder, decoder, and VM. Token assignments:
- N=1: value reference (unary pointer follows)
- N=2: instruction (instruction code token follows)
- N=3,4,5,7: structural open/close pairs

Separator between grammar and data: `00000000` (8 zeros). Justified because max grammar token was N=7 (7 zeros + 1), so any 8-zero run could only be the separator.

Five arithmetic tests passing: ADD, SUB, MUL, STORE, chained ops.

---

## Part 3 — V1 Compiler (March 1, 2026)

Simple source language: assignment, arithmetic, print. Compiler allocates a data slot for every literal, variable, and intermediate result. `x = 3 + 5` becomes: alloc(3), alloc(5), alloc(0) for result, alloc(0) for x, emit ADD(data[0], data[1], data[2]), emit LOAD(data[2], data[3]).

VM: simple PC-based loop, no jumps yet, all state in data array.

---

## Part 4 — V2: Conditionals and Loops (March 1, 2026)

### Jump token design

- N=6: jump-if-false (JIF) — condition data index + instruction target
- N=9: unconditional jump (JMP)

Jump targets as instruction indices (not bit offsets) — compiler can patch them after emitting the body.

**Forward patching:** Emit JIF with target=0, compile body, patch real target after.

### Bug 2: Separator collision with jump tokens

All conditional/loop tests failed with "Separator collision" error.

**Root cause:** N=9 token = `000000001` (9 zeros + 1). When preceded by another token's terminating 1, the sequence `1 000000001` contains 8 consecutive zeros — matching the `00000000` separator pattern. Any token with N≥8 triggered this.

**Attempted fix 1:** Change separator to `100000001`. Also failed — N=7 token `00000001` preceded by terminating 1 gives `100000001` which matches the new separator.

**Real fix:** Abandon pattern-based separation entirely. Replace separator with a **16-bit length header** at the start of the bitstream. Decoder reads 16 bits, gets exact grammar length, reads that many bits as grammar, treats remainder as data. Zero collision risk. Cleaner design — format is self-describing from the first bits.

Also upgraded data values from 8-bit to 16-bit at this point.

### Bug 3: While loop condition not re-evaluated

While loop ran once and stopped. The JMP at the end jumped past the condition check. **Fix:** Set `loop_top` to point at the CMP instruction itself, not before it. JMP jumps back to the comparison.

### Bug 4: Else block double-compiled

Block finder included the `else:` line in the then-block. **Fix:** Block finder stops when indent ≤ parent indent. The `else:` line is at same indentation as `if`, so it terminates the then-block correctly.

23/23 tests passing. Turing complete.

---

## Part 5 — V3: Functions (March 1, 2026)

### Function token design

Three new tokens/instructions:
- **FUNC (N=12):** entry marker. Encodes func_id, nparams, and exact data slot indices for each parameter — all in the bytecode itself, not in metadata.
- **CALL (N=10):** encodes func_id, nargs, result slot, arg slot indices.
- **RET (N=11):** encodes return value slot.

Param slots stored in FUNC instruction so the VM knows which slots to write args into — no external metadata needed.

### Two-pass compilation

Pass 1: scan and compile all `def` blocks, register entry points and param slots.
Pass 2: compile top-level code, which can now call any function regardless of definition order.

### Local variable isolation for recursion

On CALL: VM saves current values of all data slots belonging to the function (params + all slots allocated during body compilation). On RET: VM restores saved values, writes return value to caller's result slot.

Compiler tracks which slots are "local" by recording `len(data)` before and after compiling the function body. All newly allocated slots in that range are local to the function.

### Bug 5: FUNC skip lands inside function body

When executing top-level code linearly and hitting a FUNC instruction, the VM needs to skip the entire function body. Original approach: scan forward for the first RET (tracking depth). Failed for functions with multiple return paths (early return in if branch). Scan found the first RET, jumped to the instruction immediately after — still inside the function.

**Symptom:** `fact(5)` returned 5, the input value. Recursive call never happened.

**Fix:** Compiler emits an unconditional JMP over the entire function body before the FUNC marker. JMP target is patched after the body is fully compiled. VM never needs to scan for RET — it just executes the JMP.

### Bug 6: Compound expression in `double(x) * 2`

Expression parser checked for function calls first, before checking arithmetic operators. The check `if "(" in expr` matched `double(x) * 2`. Parser extracted `fname = double`, treated `* 2` as part of the args string or discarded it. Result: the multiplication never happened.

**Symptom:** `quad(5)` (which calls `double(x) * 2`) returned 10 instead of 20.

**Fix:** Check arithmetic operators first using a paren-aware depth scanner — finds operators at depth=0 (outside all parentheses) only. Function call detection only triggers if the expression ends with `)` and the part before `(` is a valid identifier.

### Bug 7: Recursive call arguments not evaluated

Call arguments used `_resolve()` instead of `_expr()`. `_resolve` handles simple variable names and literals only — does not evaluate expressions. `fact(n - 1)` resolved to a freshly allocated zero slot.

**Symptom:** Recursive `fact(3)` printed 3, never recursed.

**Fix:** Changed call argument resolution to `_expr()`.

### Bug 8: print couldn't call functions

`print fact(5)` used `_resolve('fact(5)')` which treated the whole string as a variable name. **Fix:** Changed print to use `_expr()`.

11/11 function + real program tests passing. Recursive factorial, fibonacci, bubble sort (variable-based), primes, GCD all correct.

---

## Part 6 — V4: Elias Coding + Arrays (March 2, 2026)

### Elias gamma for data values

Fixed-16-bit data values wasted bits on small numbers. Almost all real programs are dominated by small values: loop counters (0, 1, 2...), boolean flags (0, 1), intermediate results. Every such value paid 16 bits.

Elias gamma encoding: encode V≥0 as Elias(V+1): emit floor(log2(V+1)) leading zeros, then V+1 in binary. Self-delimiting. V=0 → 1 bit. V=10 → 7 bits. Break-even with fixed-16 is around value 200.

Applied only to the data section — not grammar stream. Grammar tokens start with zeros, so any zero-leading encoding inside the grammar would be ambiguous. Data section is parsed separately so it can use any self-delimiting encoding.

Header updated: `[16-bit grammar length][16-bit data count]` — data count needed because Elias values are variable-length and decoder must know when to stop.

### Arrays

Four new instruction codes:
- ARR_NEW (N=14): create array, store ID in data slot
- ARR_GET (N=15): read element
- ARR_SET (N=16): write element  
- ARR_LEN (N=17): get length

Arrays stored in a runtime table separate from the scalar data section. Scalar data slots hold array IDs (integers). Array passing to functions works correctly because array IDs are just integers.

### Bug 9: elif exclusion silences array reads

Statement parser had:
```python
elif "[" in s and "=" in s and not comparisons:
    if bracket < eq:
        ... array set ...
elif "=" in s and not comparisons:
    ... regular assignment ...
```

For `x = arr[0]`: `[` is in `s` and `=` is in `s`, so first `elif` matched. But `bracket(7) > eq(2)`, so the inner `if` was false. The array-set code did nothing. But the regular assignment `elif` was **never evaluated** — Python's `elif` is exclusive.

**Symptom:** Every array read silently returned 0. Array writes worked fine. Only reads were broken.

**Fix:** Merged both assignment cases under single `elif "=" in s`. Inside, detect array-set by checking whether the LHS contains `[`. No more competing elif branches.

15/15 tests. Benchmark results:

| Program | ZCE | Python co_code | Ratio |
|---|---|---|---|
| Bubble sort | 573B | 192B | 2.98x |
| Primes sieve | 328B | 204B | 0.65x |
| Fibonacci recursive | 151B | 138B | 1.10x |
| GCD Euclidean | 126B | 130B | 0.97x |

GCD beats Python's raw bytecode. Fibonacci nearly tied. Bubble sort still larger due to unary pointer cost at high slot indices.

---

## Part 7 — Redesign: Ones as Depth (March 2, 2026)

### The problem with paired tokens

The v1-v4 grammar used paired open/close tokens for structure. A block opened at N=4, content followed, N=4 closed it. The VM and compiler had to track which tokens were open/closed, reconstruct tree structure from matched pairs, and maintain context stacks. This was working but redundant — the structure was being encoded twice (implicitly in the nesting, explicitly in the pairs).

More critically: unary pointers (all-ones-then-zero) were still being used for slot references in the grammar stream. Slot index 50 cost 52 bits. With 50 data slots in a bubble sort, this was the dominant overhead.

### The proposal: ones carry depth

The terminating `1` in every grammar token was pure punctuation — it carried no information. A run of ones could carry the tree depth directly.

New token format: `[N zeros][M ones][0]`
- N zeros = token type (what this token is)
- M ones = tree depth (where in the tree it lives)
- 0 = terminator

**Decoder loop becomes three steps:**
```
count zeros  → type
count ones   → depth
read 0       → done
```

Paired tokens eliminated entirely. Every token declares its own position.

### Depth=0 as pointer escape

Previous pointer scheme (all-ones-then-zero) now collides with the depth encoding — a ones-run IS a depth value. Resolution: **depth=0 means "not a structural token, it's a pointer."** When the decoder sees M=0 (zero ones in the ones-run), it interprets the preceding N zeros as an Elias gamma pointer rather than a structural depth.

This is elegant: depth=0 is undefined in any rooted tree. Nothing lives at depth zero. Repurposing it as a pointer escape costs no structural expressiveness.

Simultaneously: pointers now use Elias gamma instead of unary. Slot 50 = 11 bits instead of 52. Slot 100 = 13 bits instead of 102. The pointer collision problem and the pointer scaling problem solve each other in a single design decision.

### Impact on functions

Functions become natural depth transitions. A function body lives at depth 2. The call site lives at depth 1. Entering a function = stepping down to a deeper depth level. Returning = stepping back up. The FUNC/CALL/RET token design stays the same mechanically, but the depth field carries the scope information directly. No separate "local slots" tracking would be needed in a fully depth-native VM (current implementation still uses save/restore for compatibility; true depth-scoped frames are the next step).

### Grammar token pointer mode vs depth=0 escape

There's a subtle design point: the depth=0 escape works as follows. When the decoder reads a token and gets M=0, it knows the preceding N zeros are not a structural node but a pointer value — specifically Elias(pointer_index). But tokens that CARRY pointer arguments (JIF, JMP, CALL, RET, FUNC) need to read those pointers after emitting their structural token. This is handled by putting the decoder into "read pointer" mode after each such token: after seeing N=6 (JIF) with some depth M, the decoder reads two Elias gamma values as (condition slot, jump target). These are not depth=0 escape tokens — they're raw Elias values read in pointer mode.

This means: grammar pointer arguments appear directly as Elias gamma bit sequences, not as depth=0 escape tokens. The decoder mode (structural vs pointer) is determined by the last structural token seen.

### Bug 10: Compound expression test expectation wrong

Test expected `3 + 2 * 4 = 20`, treating it as left-to-right `(3+2)*4`. But the new operator scanner checks `+`/`-` first (splits at first `+` at depth=0, giving `l=3`, `r=2*4`), then recursively evaluates `r` which finds `*` giving `2*4=8`, then computes `ADD(3,8)=11`. This is correct standard operator precedence. The test expectation was wrong, not the code.

**Fix:** Changed expected value from 20 to 11.

### Final test results

40/40 tests passing. Benchmark results with new token format:

| Program | ZCE | Python co_code | Python .pyc | vs co_code | vs .pyc |
|---|---|---|---|---|---|
| Bubble sort | 233B | 200B | 490B | 1.17x | 0.48x |
| Primes sieve | 161B | 204B | 476B | **0.79x** | **0.34x** |
| Fibonacci | 80B | 138B | 405B | **0.58x** | **0.20x** |
| GCD | 74B | 130B | 403B | **0.57x** | **0.18x** |

Three of four programs now beat Python's raw bytecode. The Elias pointer savings transformed the benchmark picture: v4 bubble sort was 573B, current is 233B — 2.5x improvement from better pointer encoding alone. GCD shrank from 126B to 74B.

---

## Summary of All Bugs

| # | Bug | Version | Root cause | Fix |
|---|---|---|---|---|
| 1 | Raw pointer bits read as grammar | V1 | `00000000` (index 0) matches grammar zero-run | Unary pointers (ones+zero, no zero-runs) |
| 2 | Separator collision with N≥8 tokens | V2 | N=9 token contains 8-zero run matching separator | 16-bit length header replaces pattern separator |
| 3 | While loop runs once | V2 | JMP jumped past condition check | Set loop_top to point at CMP instruction |
| 4 | Else block double-compiled | V2 | Block finder included `else:` line in then-block | Stop block finder when indent ≤ parent indent |
| 5 | FUNC skip lands inside function body | V3 | Depth-scan stopped at first RET (early return path) | Compiler emits JMP over entire function before FUNC |
| 6 | `double(x) * 2` parsed as broken func call | V3 | Func call check ran before operators; `(` matched compound expr | Check operators first (paren-aware); tighten func call detection |
| 7 | Recursive call args wrong | V3 | Call args used `_resolve()` not `_expr()` | Changed to `_expr()` |
| 8 | `print func(args)` prints zero | V3 | print used `_resolve()` not `_expr()` | Changed print to use `_expr()` |
| 9 | Array reads silently return 0 | V4 | `elif` exclusion: array-set outer elif matched but inner if was false, regular assignment elif never ran | Merge both assignment cases under single elif |
| 10 | Compound expr test wrong | Current | Expected `(3+2)*4=20`, actual standard precedence gives `3+(2*4)=11` | Fixed test expectation |

---

## Key Design Decisions

| Decision | Options considered | Choice | Reason |
|---|---|---|---|
| Pointer encoding | Raw bits, fixed-width, unary, Elias | Unary (v1-v4), Elias (current) | Unary: zero-free, safe in grammar. Elias: logarithmic cost, enabled by depth=0 escape |
| Grammar/data separation | Pattern separator, length header | Length header | Pattern separators vulnerable to collision; header is collision-proof |
| Jump targets | Bit offsets, instruction indices | Instruction indices | Compiler doesn't know bit sizes ahead of time |
| Tree structure | Paired tokens, depth-in-token | Depth-in-token (current) | Paired tokens are redundant; depth directly encodes position with no bookkeeping |
| Data encoding | Fixed-16, Elias | Elias | 80-90% savings on typical small-value dominated programs |
| Operator precedence | Left-to-right flat, standard `*/` before `+-` | Standard | Correct and expected; confirmed by test correction |

---

*Build log complete through March 2, 2026*