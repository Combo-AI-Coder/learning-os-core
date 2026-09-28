# Modern Language Models: reusable probes

Optional teaching assets. Load only when selecting a probe is relevant to
a teaching decision. These are candidate anchors, not mastery tests.
Keep learner answers, Evidence, and capability state outside this file.

## mlm.tokens-context.trace-and-change.v1

- Stable id: `mlm.tokens-context.trace-and-change.v1`
- Target node: `representation.tokens_and_context`
- Target capability: `conceptual_structure`
- Supporting capability: `explanation`

### When to use

Use when distinguishing token identity, input sequence, numerical
representation, and prediction choices could change the next action:
continue with LM computation, give a local explanation, or repair a
specific confusion.

Prefer existing conversation evidence. Do not administer merely because
the node is in progress or capability state is unknown. Skip when recent
performance already supplies the needed distinction or the learner has
just rehearsed this example.

The same scenario can support direct teaching when explanation is the
better action. Mark any subsequent response as scaffolded or recently
exposed rather than treating it as an independent diagnostic.

### Prerequisites and declared interfaces

The learner needs to read an ordered list and a lookup table. Establish
those representations inline if needed; failure to read them is not
failure of the target capability.

Assume only the basic task: use the supplied input to predict one next
token. No probability calculation, matrix multiplication, tokenization
algorithm, attention mechanism, or positional-encoding knowledge is
required.

The context-processing model is a declared black box: it receives an
ordered sequence of numerical token representations and produces
context-dependent representations used to predict the next token.
Its internal mechanism is outside this probe.

### Prompt

Consider this deliberately small, fixed language model.

Its complete input vocabulary is:

| Token spelling | Token ID |
|---|---:|
| red | 0 |
| blue | 1 |
| bird | 2 |
| sings | 3 |
| . | 4 |
| ! | 5 |

For this toy system, the output vocabulary is the same six entries.
All six are eligible next-token choices; there is no additional output
mask. Spaces separate token spellings in this example and are not
additional tokens. These are stipulated toy tokens, not a claim that
real tokenizers split text into words.

The model has one fixed input-embedding lookup vector of three numbers
for each vocabulary entry. Position-related processing, if any, happens
after that lookup. Its context window admits at most four input token
positions per call. No overflow-handling policy has been specified.

The current input is:

    red bird red

1. Trace this input from token occurrences to token IDs to embedding
   lookups. How many input positions, vocabulary entries, and numbers
   per lookup vector are involved? Explain the role of each quantity
   and whether the two occurrences of `red` start with the same vector.

2. Compare with a separate input, `blue bird red`. Is the last token's
   initial lookup vector different? Must its representation after
   context processing, or the next-token prediction, be identical?
   Could this model choose `!` even though `!` is absent from either
   input? Explain using the model's inputs and available output choices.

3. Append `sings` and then `.` to the original input. At which point
   does the full sequence exceed the stated context window? Does that
   enlarge the vocabulary or change the embedding-vector length?
   What can you conclude about processing the full sequence, and what
   additional policy would be needed?

4. Suppose IDs 0 and 5 are exchanged everywhere consistently: the
   token-to-ID mapping, input-embedding row association, and output
   entry association. All numerical parameters associated with each
   token spelling are preserved. Has the modeled meaning or behavior
   changed merely because `red` now has ID 5? Explain what an ID does.

Answer with a trace and reasons, rather than a list of definitions.
You may refer to the table throughout.

### Diagnostic rationale

The trace separates a reusable token type from its occurrences and
integer identifier. Repetition distinguishes sequence length from
vocabulary size, while the three-number vectors distinguish embedding
width from both.

Changing an earlier token while retaining the last token separates
fixed input lookup from subsequent context-dependent computation.
The absent-but-eligible output distinguishes the current context from
the output vocabulary.

Appending tokens separates current context length from a context-window
limit. Consistent renumbering checks whether the learner treats IDs as
labels or incorrectly assigns meaning to their numerical magnitude.

### Reasoning rubric

Use these as scoped observations, not a total score or pass threshold.

- Trace: `red bird red` has three token occurrences, two distinct token
  types in this input, and ID sequence `[0, 2, 0]`. The complete
  vocabulary still has six entries. Lookup produces three vectors,
  each containing three numbers. The two `red` occurrences retrieve
  the same initial vector. Three positions and three numbers per
  vector happen to coincide here; they describe independent quantities.

- Roles: a token is an entry/unit used by this tokenizer and model;
  its ID identifies that entry for lookup and output interpretation.
  The vocabulary is the complete inventory, while the current context
  is an ordered sequence of token occurrences supplied to this call.
  An embedding is a numerical representation used in computation,
  not the token ID or the entire context.

- Changed context: the last `red` has the same initial lookup vector
  in both inputs. Earlier input differs, so later contextual
  representations and predictions may differ. They are not required
  to differ, and exact outputs cannot be inferred from this setup.
  Do not require an explanation of attention or positional encoding.

- Output choices: `!` is eligible because it belongs to the output
  vocabulary. An output need not have appeared in the input context.
  Eligibility does not establish that `!` will actually be selected.
  Input and output inventories coincide by stipulation in this toy
  system; context membership does not define either inventory.

- Window: appending `sings` yields four positions; appending `.`
  yields five. The full five-position sequence exceeds this model's
  four-position input limit. Vocabulary size remains six and lookup
  vectors still contain three numbers. The setup does not specify
  whether a caller rejects, truncates, or otherwise manages overflow.
  Dropping an earlier token would change the supplied context.

- Renumbering: consistent reassociation preserves the same vectors,
  computation, and token-labeled outputs. The numerical magnitude of
  an ID is not a semantic magnitude. Changing IDs without updating
  their associations would instead select or label the wrong entries.

Correct counts without causal explanations are insufficient to resolve
the structural question. Accept equivalent language and diagrams; exact
terminology alone neither establishes nor defeats understanding.

### Known shortcuts and confounds

- Counting table rows or copying the ID sequence can produce correct
  numbers without distinguishing the objects. Ask for roles and the
  consequences of the changes.
- Word-like toy spellings can encourage "one word equals one token."
  Preserve the explicit stipulation; do not introduce a tokenizer quiz.
- "Embedding" can mean the initial lookup or a contextual representation.
  Resolve that ambiguity before interpreting a response as an error.
- Familiarity with this exact example or immediately preceding teaching
  lowers diagnosticity. Do not disguise repetition by changing colors.
- Reading load, notation, and a four-part prompt may dominate the target.
  Present one part at a time, allow spoken explanations, or stop early.
- The artificial short window tests a distinction, not knowledge of
  real model limits or serving behavior.
- The renumbering question can impose unnecessary bookkeeping. Explain
  "all associations move consistently" without requiring implementation
  details; use it only if the ID distinction remains decision-relevant.

### Learner and flow cost

Estimated full task: 4–7 minutes, plus a brief follow-up if needed.
A selected part may take 1–2 minutes. Difficulty is conceptual comparison
with light counting; there is no calculation of vector values.

Select the smallest useful part. Stop once the next teaching action is
clear; completing all four parts is not required.

### Response-pattern follow-ups

- Correct counts but only memorized labels: ask which object changes
  when one more `red` is appended, and why. If explanation is more
  useful than another question, demonstrate the distinction directly.
- ID treated as a numerical meaning or embedding: use the consistent
  renumbering comparison, then explain lookup labels versus vectors.
- Same initial embedding taken to imply identical contextual behavior:
  contrast the two inputs and explain the black-box interface. Do not
  require Transformer internals.
- Output restricted to tokens seen in context: point to `!` in the
  output inventory and contrast eligible choices with observed input.
- Window confused with vocabulary size or vector width: trace the
  append operation and label the three independent quantities.
- Overflow policy guessed as inevitable: distinguish the input limit
  from a caller's choice of rejection or context management.
- Table or notation confusion: repair that representation or switch
  to a verbal trace before drawing any target-capability inference.
- Coherent structural explanation: continue ordinary teaching toward
  LM input/computation. Do not require another probe for completeness.

A response creates no capability transition by itself. Any later
persistent judgment must follow the Evidence protocols and preserve
scope, assistance, exposure, and uncertainty.

### Provenance and licensing

Self-authored for Learning OS using a synthetic toy model and examples.
No external question, quotation, dataset, or learner material is used.

Usage follows the repository's applicable licensing terms. This asset
adds no separate license grant and makes no claim about an unspecified
repository license.
