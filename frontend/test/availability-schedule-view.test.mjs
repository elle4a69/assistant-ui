import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const apiSource = await readFile(
  new URL('../src/api.ts', import.meta.url),
  'utf8',
);

const bookingsViewSource = await readFile(
  new URL('../src/BookingsView.tsx', import.meta.url),
  'utf8',
);

const scheduleViewSource = await readFile(
  new URL('../src/AvailabilityScheduleView.tsx', import.meta.url),
  'utf8',
);

test('api.ts exports AvailabilityConfig and getAvailabilityConfig/saveAvailabilityConfig', () => {
  assert.match(apiSource, /export interface AvailabilitySlotDay/);
  assert.match(apiSource, /export interface AvailabilityConfig/);
  assert.match(apiSource, /maxDaysAhead:\s*number/);
  assert.match(apiSource, /weeklyHours:\s*AvailabilitySlotDay\[\]/);
  assert.match(apiSource, /export async function getAvailabilityConfig\(\)/);
  assert.match(apiSource, /export async function saveAvailabilityConfig\(/);
  assert.match(apiSource, /\/api\/settings\/availability/);
});

test('BookingsView integrates Availability viewMode and navigation buttons', () => {
  assert.match(bookingsViewSource, /import AvailabilityScheduleView from '\.\/AvailabilityScheduleView'/);
  assert.match(bookingsViewSource, /'day'\s*\|\s*'list'\s*\|\s*'availability'/);
  assert.match(bookingsViewSource, />\s*Availability\s*<\/button>/);
  assert.match(bookingsViewSource, />\s*Edit Availability\s*<\/span>/);
  assert.match(bookingsViewSource, /viewMode === 'availability'\s*\?\s*\(\s*<div[^>]*>\s*<AvailabilityScheduleView/);
});

test('AvailabilityScheduleView implements reference layout with 48 half-hour slots and presets', () => {
  assert.match(scheduleViewSource, /Weekly Schedule/);
  assert.match(scheduleViewSource, /Maximum Days Ahead/);
  assert.match(scheduleViewSource, /Determines how far in advance clients can book/);
  assert.match(scheduleViewSource, /Week of/);
  assert.match(scheduleViewSource, /9:00 AM – 5:00 PM/);
  assert.match(scheduleViewSource, /All Day/);
  assert.match(scheduleViewSource, /Clear/);
  assert.match(scheduleViewSource, /Save Availability/);
  assert.match(scheduleViewSource, /length:\s*48/);
  assert.match(scheduleViewSource, /bg-\[#7a0b2e\]/);
});
