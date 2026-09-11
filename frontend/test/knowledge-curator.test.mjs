import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const settingsSource = readFileSync(new URL('../src/SettingsView.tsx', import.meta.url), 'utf8');
const panelSource = readFileSync(new URL('../src/KnowledgeCuratorPanel.tsx', import.meta.url), 'utf8');
const apiSource = readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8');

const findingTypes = [
  'incompatible_active_records',
  'owner_answer_required',
  'apparently_superseded',
  'branched_supersession',
  'literal_dynamic_authority',
  'exact_duplicate',
  'invalid_metadata',
  'shared_provider_specific',
  'future_record',
  'expired_record',
  'dangling_supersession',
  'cross_topic_supersession',
  'cross_scope_supersession',
  'cyclic_supersession',
];

test('every curator finding has owner-facing explanatory copy and outcome labels', () => {
  for (const findingType of findingTypes) {
    assert.match(panelSource, new RegExp(`\\b${findingType}: \\{`), `${findingType} needs a plain-language mapping`);
  }

  assert.match(panelSource, /Customers may receive different answers/);
  assert.match(panelSource, /A saved price, time, or availability may become outdated/);
  assert.match(panelSource, /Important organising details are missing/);
  assert.match(panelSource, /Create a safer editable draft/);
  assert.match(panelSource, /Keep it private/);
  assert.match(panelSource, /Keep both answers/);
  assert.match(panelSource, /Review this next time/);
  assert.match(panelSource, /Result: \{choice\.effect\}/);
  assert.match(panelSource, /Recommended/);
  assert.match(panelSource, /Edit wording, situation or service line/);
  assert.match(panelSource, /editableRecordIds\.has\(record\.id\)/);
  assert.doesNotMatch(panelSource, /ask staff to review/i);
});

test('representative guided decision shows context, saved answers, safety, and accessible outcomes', () => {
  assert.match(panelSource, /What was found:/);
  assert.match(panelSource, /Applies to:/);
  assert.match(panelSource, /Use when:/);
  assert.match(panelSource, /Customer asks:/);
  assert.match(panelSource, /Why this needs you:/);
  assert.match(panelSource, /Record as preferred answer/);
  assert.match(panelSource, /select_current_rule', \[record\.id\]/);
  assert.match(panelSource, /aria-label=\{`Record saved guidance/);
  assert.match(panelSource, /Records your preference only\. It does not deactivate the other answer/);
  assert.match(panelSource, /This guidance changed after the check/);
  assert.match(panelSource, /until you approve it/);
  assert.doesNotMatch(panelSource, /\{proposal\.finding_type\}/);
  assert.doesNotMatch(panelSource, /proposal\.owner_questions/);
  assert.doesNotMatch(panelSource, /proposal\.proposed_action/);
  assert.doesNotMatch(panelSource, /proposal\.reason_codes/);
  assert.doesNotMatch(panelSource, /proposal\.canonical_key/);
});

test('curator explains loading, failure, first-run, and no-findings states', () => {
  assert.match(panelSource, /Loading the latest review\. No guidance is being changed/);
  assert.match(panelSource, /The guidance review could not be loaded/);
  assert.match(panelSource, /The most recent check could not finish\. Nothing changed/);
  assert.match(panelSource, /A check has not run yet/);
  assert.match(panelSource, /Nothing needs your decision right now/);
  assert.match(panelSource, /future check will show new questions here/);
  assert.match(panelSource, /AI-assisted suggestions were skipped/);
  assert.match(panelSource, /\{latest\.message\}/);
  assert.match(panelSource, /What still worked:/);
  assert.match(panelSource, /Next step:/);
  assert.match(panelSource, /Add AI credits, then run the check again/);
  assert.match(panelSource, /Update the AI API credentials, then run the check again/);
  assert.doesNotMatch(panelSource, /The optional writing helper was unavailable/);
  assert.match(panelSource, /aria-busy/);
  assert.match(panelSource, /role="status"/);
  assert.match(panelSource, /role="alert"/);
});

test('Settings keeps the proposal-only workflow and refreshes safely', () => {
  assert.match(settingsSource, /<KnowledgeCuratorPanel/);
  assert.match(settingsSource, /knowledgeCuratorLoadStatus/);
  assert.match(settingsSource, /cannot turn on, replace, or remove guidance/);
  assert.match(settingsSource, /resolveKnowledgeCuratorProposal\(proposal\.id, resolution, selectedRecordIds\)/);
  assert.match(settingsSource, /will not be used in customer replies unless you approve it/);
  assert.match(settingsSource, /question will return the next time the Curator checks/);
  assert.match(settingsSource, /err instanceof Error \? `\$\{err\.message\} Nothing changed\.`/);
  assert.match(settingsSource, /knowledge-curator' \? 'Knowledge Curator'/);
  assert.match(settingsSource, /handleEditCuratorRecord/);
  assert.match(settingsSource, /Use this guidance when…/);
  assert.match(apiSource, /applies_when: entry\.applies_when/);
});

test('curator client uses the protected settings API and explicit state transitions', () => {
  assert.match(apiSource, /\/api\/settings\/knowledge-curator\/run/);
  assert.match(apiSource, /\/api\/settings\/knowledge-curator\/proposals\/\$\{encodeURIComponent\(id\)\}\/resolve/);
  assert.match(apiSource, /KnowledgeCuratorResolution/);
  assert.match(apiSource, /safe_repairs_completed/);
  assert.match(apiSource, /interval_seconds/);
  assert.match(apiSource, /'manual' \| 'automatic'/);
});
