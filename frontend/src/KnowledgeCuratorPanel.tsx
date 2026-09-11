import type {
  KnowledgeCuratorProposal,
  KnowledgeCuratorRecordPreview,
  KnowledgeCuratorResolution,
  KnowledgeCuratorState,
} from './api';

type ReviewChoice = {
  label: string;
  explanation: string;
  resolution: KnowledgeCuratorResolution;
  effect: string;
  recommended?: boolean;
};

type FindingCopy = {
  title: string;
  summary: string;
  why: string;
  question: string;
  choices: ReviewChoice[];
};

const decideLater: ReviewChoice = {
  label: 'Review this next time',
  explanation: 'Make no change now. This question will return the next time the curator checks the guidance.',
  resolution: 'dismiss_for_now',
  effect: 'No knowledge change',
};

export const CURATOR_FINDING_COPY: Record<string, FindingCopy> = {
  incompatible_active_records: {
    title: 'Customers may receive different answers',
    summary: 'More than one approved answer covers the same customer situation, but the answers do not agree.',
    why: 'Only you can decide which business guidance is correct. Nothing will be changed until a choice is reviewed and approved.',
    question: 'Which guidance should your team use for this customer situation?',
    choices: [
      { label: 'Create an editable merge task', explanation: 'Put the conflicting answers and merge instructions in the Learning review queue. The current answers remain active until you edit and approve a replacement.', resolution: 'create_merged_draft', effect: 'Creates a review-queue task', recommended: true },
      { label: 'Keep both answers', explanation: 'Confirm that both answers are intentional and close this question.', resolution: 'keep_all_examples', effect: 'Keeps both answers active' },
      decideLater,
    ],
  },
  owner_answer_required: {
    title: 'Private or uncertain information needs your direction',
    summary: 'This guidance is currently kept out of customer replies.',
    why: 'The assistant cannot decide whether this business information is suitable for customers, so it will remain private unless you review it elsewhere.',
    question: 'What should happen to this information?',
    choices: [
      { label: 'Keep it private', explanation: 'Confirm that this information must stay out of customer replies.', resolution: 'not_an_issue', effect: 'Remains private' },
      decideLater,
    ],
  },
  apparently_superseded: {
    title: 'Older and newer guidance may both be in use',
    summary: 'The curator found versions of the same guidance that may not all be needed.',
    why: 'The order alone does not prove which version reflects your current business policy.',
    question: 'Are both versions intentionally still in use?',
    choices: [
      { label: 'Keep both versions', explanation: 'Confirm that both versions are intentional and close this question.', resolution: 'not_an_issue', effect: 'Keeps both versions' },
      decideLater,
    ],
  },
  branched_supersession: {
    title: 'One older answer has several possible replacements',
    summary: 'The saved history points to more than one newer answer.',
    why: 'Choosing the current business answer requires human judgment; the curator will not guess.',
    question: 'Is the existing branched version history intentional?',
    choices: [{ label: 'Keep the existing version history', explanation: 'Confirm that the branches are intentional and close this question.', resolution: 'not_an_issue', effect: 'Keeps the current history' }, decideLater],
  },
  literal_dynamic_authority: {
    title: 'A saved price, time, or availability may become outdated',
    summary: 'This guidance contains a value that should come from current Settings or the live calendar.',
    why: 'A fixed saved value can give customers old information. The curator can prepare safer wording, but it cannot approve that wording.',
    question: 'Would you like a safer version prepared for the review queue?',
    choices: [
      { label: 'Create a safer editable draft', explanation: 'Put an inactive version in the Learning review queue that uses live Settings or calendar data.', resolution: 'add_safe_replacement_draft', effect: 'Creates a review-queue draft', recommended: true },
      { label: 'Keep the fixed value', explanation: 'Confirm that the saved value is intentionally fixed and close this question.', resolution: 'not_an_issue', effect: 'Keeps current guidance active' },
      decideLater,
    ],
  },
  exact_duplicate: {
    title: 'The same guidance appears more than once',
    summary: 'Two or more saved items appear to say the same thing for the same customer situation.',
    why: 'They may be intentional, so the curator will not remove or combine anything on its own.',
    question: 'How should your team handle these matching items?',
    choices: [
      { label: 'Create an editable consolidation task', explanation: 'Put the matching copies and consolidation instructions in the Learning review queue. Existing copies stay unchanged until approval.', resolution: 'create_consolidation_draft', effect: 'Creates a review-queue task', recommended: true },
      { label: 'Keep separate copies', explanation: 'Confirm that the matching items are intentional and close this question.', resolution: 'keep_both_distinct', effect: 'Keeps every copy' },
      decideLater,
    ],
  },
  invalid_metadata: {
    title: 'Important organising details are missing',
    summary: 'The guidance does not clearly say where or when it should be used.',
    why: 'Without those details, the assistant may not know which customer situation the guidance belongs to.',
    question: 'Would you like an editable correction task added to the review queue?',
    choices: [
      { label: 'Create a correction draft', explanation: 'Put an editable correction task in the Learning review queue. You must fill in the missing details before approval.', resolution: 'create_metadata_repair_draft', effect: 'Creates an editable correction task', recommended: true },
      { label: 'Details are sufficient', explanation: 'Confirm that no correction is required and close this question.', resolution: 'not_an_issue', effect: 'Keeps current details' },
      decideLater,
    ],
  },
  shared_provider_specific: {
    title: 'Guidance may belong to only one service line',
    summary: 'Information shared across the business appears to mention details for a particular provider or line.',
    why: 'The curator cannot safely choose the correct line, and will not move the guidance automatically.',
    question: 'Should this remain shared between both service lines?',
    choices: [{ label: 'Keep it shared', explanation: 'Confirm that both service lines should use it and close this question.', resolution: 'not_an_issue', effect: 'Keeps guidance shared' }, decideLater],
  },
  future_record: {
    title: 'Guidance is scheduled for later',
    summary: 'This information has a future start date.',
    why: 'It is being shown so you can confirm the timing is intentional; the curator will not turn it on early.',
    question: 'Is the future start date expected?',
    choices: [{ label: 'Keep the scheduled date', explanation: 'Confirm that the future start date is intentional.', resolution: 'not_an_issue', effect: 'Keeps the current start date' }, decideLater],
  },
  expired_record: {
    title: 'Guidance may be past its end date',
    summary: 'This information is marked as ended or its saved end date has passed.',
    why: 'The curator will not remove business guidance without a person reviewing it.',
    question: 'Is the saved end date correct?',
    choices: [{ label: 'Keep the end date', explanation: 'Confirm that the guidance should remain ended.', resolution: 'not_an_issue', effect: 'Keeps guidance ended' }, decideLater],
  },
  dangling_supersession: {
    title: 'The earlier version can no longer be found',
    summary: 'This guidance refers to an older item that is no longer available.',
    why: 'The curator cannot safely rebuild the missing history or decide what should replace it.',
    question: 'Can the missing earlier version be ignored?',
    choices: [{ label: 'Ignore the missing history', explanation: 'Confirm that the missing earlier version is not required.', resolution: 'not_an_issue', effect: 'Keeps available guidance only' }, decideLater],
  },
  cross_topic_supersession: {
    title: 'A newer answer appears linked to a different topic',
    summary: 'The saved connection between an older and newer answer crosses business topics.',
    why: 'That connection could make the wrong guidance look current, so a person needs to confirm it.',
    question: 'Is this cross-topic connection intentional?',
    choices: [{ label: 'Keep this connection', explanation: 'Confirm that the cross-topic connection is intentional.', resolution: 'not_an_issue', effect: 'Keeps the current connection' }, decideLater],
  },
  cross_scope_supersession: {
    title: 'A newer answer appears linked to another service line',
    summary: 'The older and newer guidance belong to different parts of the business.',
    why: 'The curator cannot assume that a policy for one service line replaces another line’s policy.',
    question: 'Is this cross-line connection intentional?',
    choices: [{ label: 'Keep this connection', explanation: 'Confirm that the cross-line connection is intentional.', resolution: 'not_an_issue', effect: 'Keeps the current connection' }, decideLater],
  },
  cyclic_supersession: {
    title: 'The saved version history loops back on itself',
    summary: 'The guidance does not have a clear newest version.',
    why: 'The curator cannot safely choose a current answer from this history.',
    question: 'Can this version-history loop remain unchanged?',
    choices: [{ label: 'Keep the loop unchanged', explanation: 'Confirm that no history repair is required.', resolution: 'not_an_issue', effect: 'Keeps the current history' }, decideLater],
  },
};

const fallbackCopy: FindingCopy = {
  title: 'Saved guidance needs a closer look',
  summary: 'The curator found something it cannot safely decide on its own.',
  why: 'A person who understands the business needs to review it. No knowledge will change automatically.',
  question: 'Does this guidance need another review?',
  choices: [{ label: 'No correction is needed', explanation: 'Confirm that the guidance is intentional and close this question.', resolution: 'not_an_issue', effect: 'Keeps current guidance' }, decideLater],
};

const curatorWarningAction: Record<string, string> = {
  curator_model_not_configured: 'Ask an administrator to connect the AI helper, then run the check again.',
  openai_quota_exhausted: 'Add AI credits, then run the check again.',
  curator_model_inaccessible: 'Check that the selected AI model is available to this account, then run the check again.',
  curator_model_authentication_failed: 'Update the AI API credentials, then run the check again.',
  curator_model_rate_limited: 'Wait a few minutes, then run the check again.',
  curator_model_timeout: 'Run the check again. If it keeps timing out, check the AI connection.',
  curator_model_provider_error: 'Run the check again. If it keeps failing, check the AI provider status and connection.',
};

function curatorWarningNextStep(errorCode?: string | null): string {
  return curatorWarningAction[errorCode || ''] || 'Run the check again. If it keeps failing, ask an administrator to check the AI connection.';
}

function approvedInformation(record: KnowledgeCuratorRecordPreview): string {
  return record.example_reply || record.approved_reply || record.knowledge_text || record.instruction || 'The saved wording is not available. Run a new check before deciding.';
}

function approvedInformationLabel(record: KnowledgeCuratorRecordPreview): string {
  return record.example_reply || record.approved_reply ? 'Approved reply' : 'Approved information';
}

function affectedArea(proposal: KnowledgeCuratorProposal, lineLabels: Record<'primary' | 'secondary', string>): string {
  if (proposal.scope === 'primary') return lineLabels.primary;
  if (proposal.scope === 'secondary') return lineLabels.secondary;
  if (proposal.scope === 'shared') return 'Both service lines';
  return 'Internal information (not used in customer replies)';
}

type Props = {
  state: KnowledgeCuratorState;
  loadStatus: 'loading' | 'ready' | 'error';
  running: boolean;
  updatingId: string | null;
  lineLabels: Record<'primary' | 'secondary', string>;
  editableRecordIds: Set<string>;
  onRun: () => void;
  onResolve: (proposal: KnowledgeCuratorProposal, resolution: KnowledgeCuratorResolution, selectedRecordIds?: string[]) => void;
  onEditRecord: (proposalId: string, recordId: string) => void;
};

export default function KnowledgeCuratorPanel({ state, loadStatus, running, updatingId, lineLabels, editableRecordIds, onRun, onResolve, onEditRecord }: Props) {
  const latest = state.runs[0];
  const findings = state.proposals.filter(item => item.status === 'proposed' || item.status === 'accepted');

  return <section className="rounded-xl border border-violet-200 bg-violet-50/40 p-4 sm:p-5" aria-labelledby="knowledge-curator-heading" aria-busy={loadStatus === 'loading' || running}>
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div>
        <h3 id="knowledge-curator-heading" className="text-base font-bold text-violet-950">Knowledge Curator</h3>
        <p className="mt-1 max-w-2xl text-xs leading-relaxed text-violet-800">Find unclear, duplicated or outdated guidance and decide what should happen. Nothing becomes active without your approval.</p>
      </div>
      <button type="button" onClick={onRun} disabled={running || loadStatus === 'loading'} aria-label={running ? 'Checking customer guidance' : 'Check customer guidance now'} className="inline-flex min-h-11 shrink-0 items-center justify-center rounded-lg bg-violet-700 px-4 py-2.5 text-xs font-bold text-white hover:bg-violet-800 disabled:opacity-50">
        {running ? 'Checking…' : 'Check guidance now'}
      </button>
    </div>

    {loadStatus === 'loading' && <div role="status" className="mt-3 rounded-lg border border-violet-100 bg-white p-3 text-[11px] text-slate-700">Loading the latest review. No guidance is being changed.</div>}
    {loadStatus === 'error' && <div role="alert" className="mt-3 rounded-lg border border-rose-200 bg-rose-50 p-3 text-[11px] text-rose-900"><strong>The guidance review could not be loaded.</strong><br />Nothing has changed. Check again to retry safely.</div>}

    {loadStatus === 'ready' && <>
      <div className="mt-3 rounded-lg border border-violet-100 bg-white p-3 text-xs text-slate-700">
        <strong>{findings.length ? `${findings.length} item${findings.length === 1 ? '' : 's'} waiting for review` : 'No guidance questions are waiting'}</strong>
        <p className="mt-1">{latest ? `Last checked ${new Date(latest.completed_at).toLocaleString()}. ` : 'A check has not run yet. '}{findings.length ? 'Review each item below when you are ready.' : state.automation.enabled ? 'Automatic checks will add a question here if owner input is needed.' : 'Use “Check guidance now” whenever you want to look for issues.'}</p>
        {latest?.status === 'failed' ? <p className="mt-1 text-rose-800">The most recent check could not finish. Nothing changed. Run the check again when you are ready.</p>
          : latest?.error_code ? <div role="alert" className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-amber-950">
            <p className="font-bold">AI-assisted suggestions were skipped</p>
            <p className="mt-1">{latest.message}</p>
            <p className="mt-1"><strong>What still worked:</strong> The rule-based safety checks completed and no guidance was changed.</p>
            <p className="mt-1"><strong>Next step:</strong> {curatorWarningNextStep(latest.error_code)}</p>
          </div>
          : null}
        <p className="mt-1 text-violet-800">Any draft or correction task goes to the Learning review queue and stays out of customer replies until you approve it.</p>
      </div>

      {findings.length === 0 ? <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-[11px] text-emerald-900"><strong>Nothing needs your decision right now.</strong><br />Current guidance stays as it is. A future check will show new questions here.</div> : <div className="mt-3 space-y-3">
        {findings.map((proposal, index) => {
          const copy = CURATOR_FINDING_COPY[proposal.finding_type] || fallbackCopy;
          const records = (proposal.record_previews || []).filter(record => record.reference_status === 'current');
          const busy = updatingId === proposal.id;
          const changed = proposal.actionable === false;
          const alreadyPrepared = proposal.status === 'accepted';
          return <article key={proposal.id} className="rounded-lg border border-violet-200 bg-white p-3 sm:p-4" aria-labelledby={`curator-finding-${proposal.id}`}>
            <p className="text-[11px] font-bold uppercase tracking-wide text-violet-700">Review item {index + 1} of {findings.length}</p>
            <h4 id={`curator-finding-${proposal.id}`} className="mt-1 text-sm font-bold text-slate-900">{copy.title}</h4>
            <p className="mt-2 text-xs leading-relaxed text-slate-700"><strong>What was found:</strong> {copy.summary}</p>
            <p className="mt-1 text-xs leading-relaxed text-slate-700"><strong>Applies to:</strong> {affectedArea(proposal, lineLabels)}</p>

            {records.length > 0 && <div className="mt-3 grid gap-2 md:grid-cols-2">
              {records.map((record, recordIndex) => <div key={`${record.id}-${record.revision}`} className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-700">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="font-bold text-slate-900">Saved guidance {records.length > 1 ? recordIndex + 1 : ''}</p>
                  {record.review_status === 'approved' && <span className="rounded-full bg-emerald-100 px-2 py-1 text-[10px] font-bold uppercase tracking-wide text-emerald-800">Already approved</span>}
                </div>
                {record.applies_when && <p className="mt-1"><strong>Use when:</strong> {record.applies_when}</p>}
                {record.customer_message && <p className="mt-1"><strong>Customer asks:</strong> {record.customer_message}</p>}
                <p className="mt-1 whitespace-pre-wrap"><strong>{approvedInformationLabel(record)}:</strong> {approvedInformation(record)}</p>
                {record.instruction && record.instruction !== approvedInformation(record) && <p className="mt-1 whitespace-pre-wrap text-slate-600"><strong>Supporting rule:</strong> {record.instruction}</p>}
                {editableRecordIds.has(record.id) && <><button type="button" onClick={() => onEditRecord(proposal.id, record.id)} disabled={busy} className="mt-3 min-h-11 w-full rounded border border-indigo-300 bg-indigo-50 px-2 py-2 font-bold text-indigo-900 hover:bg-indigo-100 disabled:opacity-50">Clarify or correct this information</button><p className="mt-1 text-[11px] text-slate-600">Edit the exact saved item, then approve your correction to close this question.</p></>}
                {proposal.finding_type === 'incompatible_active_records' && !changed && !alreadyPrepared && <>
                  <button type="button" onClick={() => onResolve(proposal, 'select_current_rule', [record.id])} disabled={busy} aria-label={`Record saved guidance ${recordIndex + 1} as the preferred answer. This does not change customer guidance.`} className="mt-3 min-h-11 w-full rounded border border-violet-300 bg-white px-2 py-2 font-bold text-violet-900 hover:bg-violet-50 disabled:opacity-50">Record as preferred answer</button>
                  <p className="mt-1 text-[11px] text-slate-600">Records your preference only. It does not deactivate the other answer.</p>
                </>}
              </div>)}
            </div>}

            <p className="mt-3 text-xs leading-relaxed text-slate-700"><strong>Why this needs you:</strong> {copy.why}</p>
            {records.some(record => record.review_status === 'approved') && <p className="mt-2 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs leading-relaxed text-emerald-950"><strong>This information is already approved.</strong> You are not being asked to approve it again. The question below is only about the separate issue described above.</p>}
            <p className="mt-2 text-sm font-bold text-slate-900">{copy.question}</p>

            {changed ? <div role="alert" className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-[11px] text-amber-900"><strong>This guidance changed after the check.</strong><br />For safety, these choices are unavailable. Run a new check to review the latest wording.</div>
              : alreadyPrepared ? <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-[11px] text-emerald-900"><strong>Safer wording is waiting in the review queue.</strong><br />It is not being used in customer replies. Current guidance remains unchanged until the separate approval step.</div>
              : <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {copy.choices.map(choice => <button key={choice.resolution} type="button" onClick={() => onResolve(proposal, choice.resolution)} disabled={busy} aria-label={`${choice.label}. ${choice.explanation}`} className={`min-h-14 rounded-lg border bg-white px-3 py-3 text-left text-xs hover:border-violet-400 hover:bg-violet-50 disabled:opacity-50 ${choice.recommended ? 'border-violet-400 ring-1 ring-violet-100' : 'border-slate-300'}`}>
                  <span className="flex items-center justify-between gap-2 font-bold text-slate-900"><span>{choice.label}</span>{choice.recommended && <span className="rounded-full bg-violet-100 px-2 py-0.5 text-[10px] text-violet-800">Recommended</span>}</span>
                  <span className="mt-1 block leading-relaxed text-slate-600">{choice.explanation}</span>
                  <span className="mt-2 block text-[10px] font-bold uppercase tracking-wide text-violet-700">Result: {choice.effect}</span>
                </button>)}
                {busy && <p role="status" className="col-span-full text-xs font-semibold text-violet-800">Saving your choice…</p>}
              </div>}
          </article>;
        })}
      </div>}
    </>}
  </section>;
}
