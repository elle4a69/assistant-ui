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
  assert.match(panelSource, /Prepare safer guidance for review/);
  assert.match(panelSource, /Keep it out of customer replies/);
  assert.match(panelSource, /Both are correct in different situations/);
  assert.match(panelSource, /Leave unchanged and ask staff to review/);
  assert.match(panelSource, /Decide later/);
});

test('representative guided decision shows context, saved answers, safety, and accessible outcomes', () => {
  assert.match(panelSource, /What the curator found:/);
  assert.match(panelSource, /Affected guidance:/);
  assert.match(panelSource, /Use when:/);
  assert.match(panelSource, /Customer asks:/);
  assert.match(panelSource, /Why your input matters:/);
  assert.match(panelSource, /Choose this answer for staff review/);
  assert.match(panelSource, /select_current_rule', \[record\.id\]/);
  assert.match(panelSource, /aria-label=\{`Choose saved guidance/);
  assert.match(panelSource, /Records your choice only\. Customer guidance is not changed/);
  assert.match(panelSource, /This guidance changed after the check/);
  assert.match(panelSource, /separate review step|separate approval step/);
  assert.match(panelSource, /stays out of customer replies until someone approves it/);
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
  assert.match(panelSource, /aria-busy/);
  assert.match(panelSource, /role="status"/);
  assert.match(panelSource, /role="alert"/);
});

test('Settings keeps the proposal-only workflow and refreshes safely', () => {
  assert.match(settingsSource, /<KnowledgeCuratorPanel/);
  assert.match(settingsSource, /knowledgeCuratorLoadStatus/);
  assert.match(settingsSource, /cannot turn on, replace, or remove guidance/);
  assert.match(settingsSource, /resolveKnowledgeCuratorProposal\(proposal\.id, resolution, selectedRecordIds\)/);
  assert.match(settingsSource, /will not be used in customer replies unless separately approved/);
  assert.match(settingsSource, /could not be saved because the guidance may have changed/);
  assert.match(settingsSource, /window\.confirm\('Clear all curator questions and start again\?/);
  assert.match(settingsSource, /Approved guidance, learnings, settings, customer conversations, and bookings will not be changed/);
  assert.match(settingsSource, /clearKnowledgeCuratorQuestions\(\)/);
  assert.match(settingsSource, /setKnowledgeCurator\(result\.state\)/);
});

test('curator reset control is explicit, confirmed, and handles an empty reset', () => {
  assert.match(panelSource, /Clear curator questions and start again/);
  assert.match(panelSource, /onClick=\{onClear\}/);
  assert.match(panelSource, /disabled=\{running \|\| clearing \|\| loadStatus !== 'ready'\}/);
  assert.match(settingsSource, /There were no curator questions to clear\. You can run a fresh check now/);
  assert.match(settingsSource, /Curator questions could not be cleared\. Nothing changed/);
});

test('curator client uses the protected settings API and explicit state transitions', () => {
  assert.match(apiSource, /\/api\/settings\/knowledge-curator\/run/);
  assert.match(apiSource, /\/api\/settings\/knowledge-curator\/clear-questions/);
  assert.match(apiSource, /\/api\/settings\/knowledge-curator\/proposals\/\$\{encodeURIComponent\(id\)\}\/resolve/);
  assert.match(apiSource, /KnowledgeCuratorResolution/);
  assert.match(apiSource, /safe_repairs_completed/);
  assert.match(apiSource, /interval_seconds/);
  assert.match(apiSource, /'manual' \| 'automatic'/);
});
