import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const apiSource = readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8');
const viewSource = readFileSync(new URL('../src/SmsClientView.tsx', import.meta.url), 'utf8');

test('SMS simulator uses the authenticated admin API with account-scoped input', () => {
  const simulatorClient = apiSource.slice(
    apiSource.indexOf('export async function sendCustomerSms'),
    apiSource.indexOf('export async function listBookings'),
  );

  assert.match(simulatorClient, /\/api\/admin\/sms-simulator/);
  assert.match(simulatorClient, /customer_phone: customerPhone/);
  assert.match(simulatorClient, /sms_account_key: smsAccountKey/);
  assert.doesNotMatch(simulatorClient, /\/webhooks\/sms|\+15557654321/);
});

test('SMS simulator exposes both lines, editable phone, and backend errors', () => {
  assert.match(viewSource, /Tori \(primary\)/);
  assert.match(viewSource, /Anonymous \(secondary\)/);
  assert.match(viewSource, /value=\{customerPhone\}/);
  assert.match(viewSource, /role="alert"/);
  assert.match(viewSource, /t\.smsAccountKey === smsAccountKey/);
});
