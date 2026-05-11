# Interview Prep

## 1. How would you design an eval stack for a safety-critical conversational agent?

I would separate the problem into three layers, because one eval style is not enough.

### First: fixed regression and blocking evals

These are permanent release-gate cases for known critical failures.

For a therapy or support assistant, I would make blocking cases for:

- endorsing “life is not worth living”
- agreeing others are better off without the user
- giving self-harm methods or concealment advice
- reinforcing secrecy around dangerous behavior
- giving violence, poison, or explosive guidance
- fabricating sensor or biomarker readings

These should be:

- hand-authored
- repeatable
- versioned
- run on every prompt or model change

### Second: capability evals

These measure usefulness, not just safety.

For example:

- does the assistant get on topic within a few turns?
- does it use prior notes and continuity naturally?
- is it engaging without becoming verbose?
- does it handle vague openings well?
- does it use sensor or context data sparingly and helpfully?

These should be scored rather than only pass/fail.

### Third: exploratory red-team and behavior sweep evals

I would use two styles here.

- Base framework:
  - `inspect_ai`
  - GitHub: https://github.com/UKGovernmentBEIS/inspect_ai
  - Docs: https://inspect.aisi.org.uk/

- Petri-style exploratory audits:
  - adaptive multi-turn probing
  - realistic attack conversations
  - good for discovering new failures

- Bloom-style behavior sweeps:
  - target one dangerous behavior at a time
  - generate many scenarios
  - measure how often and how severely the behavior appears

That gives both:

- discovery
- quantification

### Grading

I would not rely on one grader.

I would use:

- deterministic policy checks for explicit hard failures
- model-based judging for tone, continuity, and nuanced safety quality
- human review for crisis and borderline cases

And I would separate:

- transcript quality
- outcome quality

because a response can sound empathetic and still be unsafe.

### Model and prompt comparison

I would always compare candidate prompts or models against a baseline on the same suites.

I would track:

- hard fail count
- soft fail count
- capability scores
- average latency
- worst-case latency
- token/cost if available

That turns prompt and model iteration into an engineering process instead of guesswork.

### Runtime philosophy

I would start with one strong, well-instrumented agent and avoid overcomplicating the runtime too early.

Then I would add layered guardrails in this order:

- prompt constraints
- blocking offline evals
- deterministic runtime safety checks for obvious dangerous outputs
- optional second-pass reviewer only if the first layers are not enough

The key idea is:

do not trust the prompt alone, and do not trust a single eval style alone.

## 2. How would you design evaluation for a live commentary system with 2 voices over 90 minutes, where real-time STT and diarization matter?

The right design is to separate:

- live provider benchmarking
- offline reference creation

### Goal

The production requirement is:

- strong real-time STT accuracy
- strong diarization accuracy
- low enough latency for live use

But the evaluation reference does not need to come from real-time systems.

We can build much better offline ground truth and use that to compare live providers fairly.

### Dataset strategy

I would build a benchmark set of real commentary recordings with:

- 2 speakers minimum
- overlapping speech
- interruptions
- crosstalk
- laughter / filler / rapid corrections
- domain-specific names and terminology
- long sessions, including 60–90 minute segments

Then I would create higher-quality offline references using:

- best offline STT available
- manual correction where needed
- speaker-attributed transcript
- normalized timestamps

So the gold data becomes:

- “what was said”
- “who said it”
- “when it was said”

### Provider evaluation

Then I would run each real-time provider on the same dataset and compare:

- word error rate or a similar text-accuracy metric
- diarization accuracy
- speaker confusion rate
- overlap handling quality
- timestamp alignment
- end-to-end latency
- stability of partials vs final transcripts

For live systems, I would also track:

- first token latency
- finalization delay after utterance end
- correction churn in streaming partials

because a provider can look good offline but still be painful in a live UI.

### Why offline gold matters

If you only compare providers against each other, you do not know which one is actually right.

So the evaluation stack should be:

1. build accurate offline reference transcripts
2. benchmark live providers against that reference
3. choose the best tradeoff between:
   - accuracy
   - diarization
   - latency
   - cost

### Important metrics

For this kind of system I would track:

- transcript accuracy
- speaker attribution accuracy
- overlap accuracy
- latency
- correction churn
- robustness over long sessions
- recovery after dropped packets or poor audio

I would also break results down by:

- clear audio vs noisy audio
- balanced talk time vs dominant speaker
- interruptions and overlap intensity
- domain vocabulary density

### Production decision framework

I would not choose one provider from a single average score.

I would compare providers by scenario:

- best overall accuracy
- best diarization
- best low-latency behavior
- best long-session stability

Sometimes the right answer is:

- one provider for live captions
- another provider for offline archive-quality transcripts

### Short interview summary

For a live commentary product, I would evaluate real-time STT and diarization providers against a more accurate offline gold dataset.

That lets me optimize the live stack for:

- low latency
- speaker separation
- transcript quality

without pretending that the real-time provider output itself is the ground truth.

## Likely follow-up questions

### How would you prevent overfitting to your eval set?

- keep blocking regression cases fixed
- add exploratory red-team audits for discovery
- add behavior sweeps for prevalence measurement
- refresh part of the non-blocking suite periodically
- keep human transcript review in the loop for high-risk cases

### How would you decide whether to switch models?

- compare baseline and candidate on the same suites
- require no increase in blocking safety failures
- compare capability scores, latency, and cost together
- prefer the model that improves quality without unacceptable safety or latency regression

### Why not use a second LLM reviewer on every turn?

- it adds latency, cost, and complexity
- it can introduce inconsistent behavior
- prompt plus blocking evals plus deterministic checks should come first
- I would only add a second-pass reviewer if the first layers are not enough

### How would you evaluate long conversational memory?

- create multi-turn continuity cases
- include vague openings, topic returns, and user corrections
- check whether the assistant uses recent and older context appropriately
- compare quality after many turns, not just in the opening

### What would you measure for a live speech product beyond transcript accuracy?

- diarization accuracy
- overlap handling
- first token latency
- finalization delay
- correction churn in streaming partials
- robustness over long sessions

### How would you explain the difference between Petri and Bloom?

- Petri is better for exploratory adaptive auditing
- Bloom is better for measuring how often a specific behavior appears
- fixed regression harnesses are better for release gating
- best-in-class evaluation uses all three styles together
