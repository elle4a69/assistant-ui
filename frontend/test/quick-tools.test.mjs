import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const sheet = await readFile(new URL('../src/QuickToolsSheet.tsx', import.meta.url), 'utf8')
const inbox = await readFile(new URL('../src/MobileInboxView.tsx', import.meta.url), 'utf8')
const api = await readFile(new URL('../src/api.ts', import.meta.url), 'utf8')

test('quick tools provides three editable persisted text buttons', () => {
  assert.match(sheet, /const LONG_PRESS_MS = 520/)
  assert.match(sheet, /label: 'ADDR'/)
  assert.match(sheet, /label: 'LINK'/)
  assert.match(sheet, /label: 'INFO'/)
  assert.match(sheet, /maxLength=\{6\}/)
  assert.match(sheet, /saveQuickReply\(accountKey, editingIndex/)
  assert.match(sheet, /onInsert\(reply\.content\)[\s\S]*onClose\(\)/)
})

test('quick tools calendar stays in the sheet and loads duration-aware availability', () => {
  assert.match(sheet, /data-testid="quick-tools-sheet"/)
  assert.match(sheet, /setView\('calendar'\)/)
  assert.match(sheet, /getFreeBusy\(selectedService\.duration\)/)
  assert.match(sheet, /See live availability without leaving this chat/)
})

test('mobile inbox opens tools and inserts saved text at the composer cursor', () => {
  assert.match(inbox, /aria-label="Open quick tools"/)
  assert.match(inbox, /ref=\{composerRef\}/)
  assert.match(inbox, /setSelectionRange\(caret, caret\)/)
  assert.match(inbox, /onInsert=\{insertQuickText\}/)
  assert.match(api, /\/api\/settings\/quick-replies\/\$\{accountKey\}/)
})
