# Zero-Count Encoding System (ZCE)
### Version 1.0 — Full System Blueprint

---

## Origin

This system came from a single intuition: **what if zeros in a bitstream weren't data, but grammar?** Instead of treating every bit as carrying equal weight, what if the *count* of consecutive zeros told you how to read everything that follows — no lookup tables, no external schema, no format specification needed?

That intuition, developed from scratch, produced a complete self-describing binary encoding system, a bytecode format, a compiler, and a virtual machine. This document describes every part of it.

---

## Core Philosophy

In conventional binary, every bit position is equivalent — just a power of 2. In ZCE, bits have two completely different roles:

- **Zeros** are grammar. They are structural markers. They cost nothing computationally — the decoder just counts them.
- **Ones** terminate zero-sequences. A `1` always ends a grammar token.
- **Data** lives separately, completely isolated from grammar. It is raw and untouched.

This means the bitstream is divided into two regions:

```
[ GRAMMAR SECTION ] 00000000 [ DATA SECTION ]
```

The grammar section describes structure, instructions, and references. The data section holds raw values. The two never mix, so zeros in your data can never be misread as grammar.

---

## The Grammar Token

The single primitive of the entire system is the **grammar token**:

```
N zeros followed by a terminating 1
```

Written as: `000...0001` where the count of zeros is N.

The count N is the entire meaning. No table. No context needed (in Version 1). Just count the zeros, hit the 1, and you know what's coming next.

### Token Table

| Zero Count (N) | Meaning          | Paired? |
|---------------|------------------|---------|
| 1             | Value reference  | No      |
| 2             | Instruction      | No      |
| 3             | Unit open/close  | Yes     |
| 4             | Block open/close | Yes     |
| 5             | Section open/close | Yes   |
| 7             | Document open/close | Yes  |

N=6 and N=8+ are reserved for future use.

**Paired tokens** work like HTML tags — open and close by context. The first occurrence of N=3 opens a unit. The second occurrence of N=3 closes it. Nesting is tracked by a simple stack.

---

## The Separator

The grammar and data sections are divided by exactly 8 consecutive zeros:

```
00000000
```

This is guaranteed to never appear in the grammar section because:
- The maximum grammar token length is N=7 (7 zeros + 1)
- Unary pointers (see below) are encoded as ones followed by a zero — they add no zero-runs
- Therefore 8 consecutive zeros can only ever be the separator

---

## Value References and Unary Pointers

When the decoder encounters N=1 (a value reference token), it immediately reads a **unary pointer** that follows. A unary pointer encodes a data index as:

```
(index + 1) ones, followed by a zero
```

Examples:
- `data[0]` → `10`
- `data[1]` → `110`
- `data[2]` → `1110`
- `data[5]` → `111111 0`

Decoding: count the ones until you hit a zero. That count minus 1 is the index.

This encoding was chosen because unary pointers contain no zero-runs, which means they can never interfere with grammar tokens or the separator. The grammar stream stays completely unambiguous.

---

## Instructions

When the decoder encounters N=2 (an instruction token), it immediately reads another grammar token whose zero-count identifies the operation:

| Instruction Zero-Count | Operation |
|------------------------|-----------|
| 1                      | ADD       |
| 2                      | SUB       |
| 3                      | MUL       |
| 4                      | STORE     |
| 5                      | LOAD      |
| 6                      | PRINT     |

No opcode table is needed. The count of zeros after the instruction marker IS the instruction.

A full instruction in the bitstream looks like:

```
[N=4 block open] [N=3 unit open]
  [N=2 instruction marker] [N=K instruction code]
  [N=1 value ref] [unary pointer]
  [N=1 value ref] [unary pointer]
  [N=1 value ref] [unary pointer]   ← result slot
[N=3 unit close] [N=4 block close]
```

---

## The Data Section

The data section is a flat array of 8-bit values (bytes), laid out sequentially after the separator. Each value is stored as exactly 8 raw bits. No encoding, no escaping, no interpretation.

Values are referenced by index via unary pointers in the grammar section. The VM reads and writes data values by index during execution.

Current limitation: values are 8-bit unsigned integers (0–255). Multi-byte values are a planned extension.

---

## A Complete Worked Example

**Source program:**
```
x = 3 + 5
```

**Compilation steps:**
1. Literal `3` → stored in data[0]
2. Literal `5` → stored in data[1]
3. Result slot for ADD → data[2] (initialized to 0)
4. Variable `x` → data[3] (initialized to 0)
5. Emit: ADD(data[0], data[1], data[2])
6. Emit: LOAD(data[2], data[3])

**Data section:**
```
[3, 5, 0, 0]
= 00000011 00000101 00000000 00000000
```

**Grammar section (annotated):**
```
0000000 1    ← document open  (N=7)
00000 1      ← section open   (N=5)

  00001      ← block open     (N=4)
  0001       ← unit open      (N=3)
    001      ← instruction    (N=2)
    01       ← ADD            (N=1)
    01       ← value ref      (N=1)
    10       ← unary ptr → data[0] = 3
    01       ← value ref      (N=1)
    110      ← unary ptr → data[1] = 5
    01       ← value ref      (N=1)
    1110     ← unary ptr → data[2] = result slot
  0001       ← unit close     (N=3)
  00001      ← block close    (N=4)

  00001      ← block open     (N=4)
  0001       ← unit open      (N=3)
    001      ← instruction    (N=2)
    00001    ← LOAD           (N=5)
    01       ← value ref      (N=1)
    1110     ← unary ptr → data[2]
    01       ← value ref      (N=1)
    11110    ← unary ptr → data[3]
  0001       ← unit close     (N=3)
  00001      ← block close    (N=4)

00000 1      ← section close  (N=5)
0000000 1    ← document close (N=7)

00000000     ← SEPARATOR

00000011 00000101 00000000 00000000   ← data: [3, 5, 0, 0]
```

**Execution:**
- ADD: data[0] + data[1] → data[2] (3 + 5 = 8)
- LOAD: data[2] → data[3] (x = 8)

---

## The Decoder Algorithm

The entire decoder is a single loop with one rule:

```
loop:
  count consecutive zeros → N
  consume the terminating 1
  
  if N == 7: toggle document context
  if N == 5: toggle section context
  if N == 4: toggle block context (on close, commit current instruction)
  if N == 3: toggle unit context
  if N == 2: read next token as instruction code
  if N == 1: read unary pointer, look up data value
  
  repeat
```

No tables. No branches on opcode values. No external specification needed. The bitstream is fully self-describing — any decoder that knows these 7 rules can read any ZCE bitstream ever produced.

---

## The Compiler (ZC-Lang v1)

ZC-Lang is a minimal source language that compiles to ZCE bytecode. Version 1 supports:

- Integer literals: `3`, `42`, `255`
- Variables: `x`, `result`, any alphanumeric name
- Arithmetic: `+`, `-`, `*`
- Assignment: `x = expr`
- Output: `print x`
- Chained operations: `y = x * 2`

**Example programs:**

```
# Basic arithmetic
x = 3 + 5
print x
# output: 8
```

```
# Chain of operations
x = 3 + 5
y = x * 2
print y
# output: 16
```

```
# Multiple variables
a = 10 + 5
b = a - 3
c = b * 4
print a
print b
print c
# output: 15, 12, 48
```

**Compilation pipeline:**

```
Source text
    ↓  Lexer (split lines, identify tokens)
    ↓  Parser (assign = expr, print stmt)
    ↓  Code generator (allocate data slots, emit instructions)
    ↓  Encoder (grammar tokens + data section)
ZCE Bitstream
    ↓  Decoder (count zeros, resolve pointers)
    ↓  VM (execute instruction list against data array)
Output
```

---

## The Virtual Machine

The VM is a simple interpreter that operates on a flat data array:

```
for each instruction:
  ADD   data[a] + data[b] → data[c]
  SUB   data[a] - data[b] → data[c]
  MUL   data[a] * data[b] → data[c]
  LOAD  data[a] → data[b]
  PRINT output data[a]
```

No registers. No stack. All state lives in the data array. This is intentional — the data section of the bytecode IS the memory of the program.

---

## What Makes This Different

| Property | ZCE | JVM Bytecode | x86 | Protobuf |
|----------|-----|-------------|-----|----------|
| Self-describing | Yes — fully | No — needs JVM spec | No | No — needs .proto file |
| Decoder size | ~50 lines | Thousands | Millions | Hundreds |
| External schema needed | No | Yes | Yes | Yes |
| Variable-length instructions | Yes — naturally | Partial | Partial | Yes |
| Grammar is table-free | Yes | No | No | No |

The key differentiator: **ZCE bitstreams carry their own complete grammar.** Any decoder anywhere that knows the 7 token rules can decode any ZCE bitstream without any external document, schema, or specification.

---

## Current Limitations (V1)

- Values are 8-bit only (0–255)
- No conditionals (if/else)
- No loops
- No functions
- No negative numbers
- Unary pointers become long for large data arrays (data[100] = 101 ones)
- Grammar overhead is significant at small scale

---

## Planned Extensions (V2 and beyond)

**Conditionals** — N=6 token as if/else marker:
```
if x > 0:
    y = x + 1
```

**Loops** — N=9 token as loop marker

**Functions** — N=10 token as function definition/call

**16-bit and 32-bit values** — extend data section to support wider types

**Compressed pointers** — replace unary pointers with fixed-width or Elias-coded pointers for large data arrays

**Mode shifting** — use a special token to switch data interpretation (unicode text, float, etc.)

**Turing completeness** — adding conditionals and loops makes ZCE Turing complete, meaning it can express any computation

---

## File Reference

| File | Description |
|------|-------------|
| `zerocount.py` | Core encoder/decoder for ZCE bytecode with tests |
| `zclang.py` | ZC-Lang compiler + VM (full pipeline source → output) |
| `SYSTEM.md` | This document |

---

## Summary

Zero-Count Encoding is a binary format where:

1. Consecutive zeros are the only grammar primitive
2. The count of zeros determines meaning — no tables
3. Data is completely separated from grammar
4. Any decoder knowing 7 rules can read any bitstream
5. A working compiler and VM exist for version 1

It is not better than JVM or x86 today. It is a clean, minimal foundation — built in one day from a raw intuition about bits — with a genuinely novel property: **total self-description with zero external dependencies.**

---

*ZCE v1 — conceived and developed March 2026*