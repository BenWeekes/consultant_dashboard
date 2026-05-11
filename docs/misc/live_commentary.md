# Live Commentary STT Eval Plan

## Goal

Evaluate real-time speech-to-text and diarization providers for a live commentary product where:

- there may be 2 voices speaking for long periods
- the system needs low enough latency for live use
- diarization quality matters, not just transcript accuracy
- we do not want to manually transcribe every live session

The right approach is to separate:

- live provider benchmarking
- offline reference creation

## Core evaluation question

Which provider gives the best tradeoff between:

- transcript accuracy
- diarization accuracy
- overlap handling
- live latency
- streaming stability
- long-session robustness

## Evaluation design

## 1. Build an offline gold dataset

Do not use one live provider as the reference for another.

Instead, build a benchmark dataset of commentary recordings with:

- 2 speakers minimum
- long sessions, including 60 to 90 minutes
- interruptions
- crosstalk
- overlapping speech
- laughter and filler
- rapid corrections
- domain-specific names and terminology
- variable audio quality

Then create a higher-quality offline reference using:

- the best offline STT available
- manual correction where needed
- speaker-attributed transcript
- normalized timestamps

The gold data should answer:

- what was said
- who said it
- when it was said

## 2. Benchmark live providers against the gold dataset

Run each real-time provider on the same recordings and compare:

- transcript accuracy
- speaker attribution accuracy
- overlap handling quality
- timestamp alignment
- latency
- stability of partial vs final transcripts

This lets you compare providers fairly using the same ground truth.

## 3. Measure real-time product behavior, not just final text

For live products, offline accuracy alone is not enough.

Also measure:

- first token latency
- finalization delay after utterance end
- correction churn in streaming partials
- stability of speaker labels during streaming
- recovery after audio glitches or packet loss

A provider can look good offline and still feel bad in a live UI if it constantly rewrites partials or flips speakers.

## Main metrics

Track at least:

- transcript accuracy
- diarization accuracy
- speaker confusion rate
- overlap accuracy
- timestamp quality
- first token latency
- finalization latency
- partial transcript churn
- long-session stability
- cost

If you want one concise product view, report both:

- offline accuracy metrics
- live experience metrics

## Dataset breakdowns

Break results down by scenario rather than relying on one average.

Useful slices:

- clear audio vs noisy audio
- balanced speakers vs one dominant speaker
- low overlap vs high overlap
- short clips vs long sessions
- common vocabulary vs domain-heavy vocabulary

This helps avoid choosing a provider that looks strong on average but fails in the exact cases that matter most.

## Human role

Humans should not have to transcribe every production session.

Instead, use humans for:

- creating and correcting the offline gold set
- spot-checking difficult overlap cases
- reviewing diarization mistakes
- validating whether a provider’s output is actually usable in the product

So the human role is:

- gold dataset creation
- calibration
- review of edge cases

not endless manual transcription of live operations.

## Recommended eval workflow

### Phase 1: Build the benchmark set

- collect representative commentary recordings
- select a manageable evaluation subset
- create high-quality offline transcripts with speaker attribution

### Phase 2: Run provider comparisons

For each provider, run the same set and capture:

- transcript output
- speaker labels
- timestamps
- streaming behavior
- latency

### Phase 3: Score and compare

Compare providers on:

- transcript accuracy
- diarization quality
- overlap handling
- latency
- long-session robustness

### Phase 4: Choose by scenario, not only average

Identify:

- best overall provider
- best diarization provider
- best low-latency provider
- best long-session provider

Sometimes the right architecture is:

- one provider for live captions
- another provider for higher-quality offline archive transcripts

## Recommended grading approach

Use two classes of scoring.

### A. Objective metrics

- transcript error rate
- speaker attribution accuracy
- overlap accuracy
- latency metrics
- churn metrics

### B. Product usability review

Human review for:

- whether the live transcript feels stable enough
- whether diarization flips are acceptable
- whether the transcript is usable for the intended live UI

This matters because a mathematically decent provider can still be poor in the actual product experience.

## Concrete output of the eval program

Each provider evaluation should produce:

- overall scorecard
- scenario breakdowns
- latency summary
- diarization summary
- sample error transcripts
- recommendation for:
  - live use
  - offline archive use

## Bottom line

For live commentary STT, the best approach is:

- build a more accurate offline gold dataset
- benchmark real-time providers against it
- measure both transcript quality and live UX behavior
- use humans for gold creation and calibration, not constant full transcription

That gives you a practical, scalable evaluation system for selecting and improving live commentary STT and diarization providers.
