const test = require('node:test');
const assert = require('node:assert/strict');

const { extractResponseText, isFailureResponse } = require('./rtm_probe');

test('extractResponseText accepts plain and structured assistant messages', () => {
  assert.equal(extractResponseText('Probe healthy'), 'Probe healthy');
  assert.equal(
    extractResponseText(JSON.stringify({ data: { text: 'Structured reply' } })),
    'Structured reply'
  );
});

test('failure response matching tolerates punctuation and capitalization', () => {
  assert.equal(isFailureResponse('Sorry, something went wrong.'), true);
  assert.equal(isFailureResponse('SOMETHING-WENT-WRONG'), true);
  assert.equal(isFailureResponse('MINDFIX_PROBE_OK_123'), false);
});
