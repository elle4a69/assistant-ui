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
};

type FindingCopy = {
  title: string;
  summary: string;
  why: string;
  question: string;
  choices: ReviewChoice[];
};

const decideLater: ReviewChoice = {
  label: 'Decide later',
  explanation: 'Leave the guidance unchanged and keep this question for a future check.',
  resolution: 'dismiss_for_now',
};

const closerReview: ReviewChoice = {
  label: 'Leave unchanged and ask staff to review',
  explanation: 'Make no knowledge change and note that this needs a closer look.',
  resolution: 'needs_manual_investigation',
};

export const CURATOR_FINDING_COPY: Record<string, FindingCopy> = {
  incompatible_active_records: {
    title: 'Customers may receive different answers',
    summary: 'More than one approved answer covers the same customer situation, but the answers do not agree.',
    why: 'Only you can decide which business guidance is correct. Nothing will be changed until a choice is reviewed and approved.',
    question: 'Which guidance should your team use for this customer situation?',
    choices: [
      { label: 'Prepare one combined answer for review', explanation: 'Create a new suggestion for staff to edit and approve separately. Current answers stay active for now.', resolution: 'create_merged_draft' },
      { label: 'Both are correct in different situations', explanation: 'Keep both answers unchanged and close this question.', resolution: 'keep_all_examples' },
      closerReview,
      decideLater,
    ],
  },
  owner_answer_required: {
    title: 'Private or uncertain information needs your direction',
    summary: 'This guidance is currently kept out of customer replies.',
    why: 'The assistant cannot decide whether this business information is suitable for customers, so it will remain private unless you review it elsewhere.',
    question: 'What should happen to this information?',
    choices: [
      { label: 'Keep it out of customer replies', explanation: 'Leave the information private and make no knowledge change.', resolution: 'needs_manual_investigation' },
      { label: 'This is safe as it is', explanation: 'Close this finding without changing the saved information.', resolution: 'not_an_issue' },
      decideLater,
    ],
  },
  apparently_superseded: {
    title: 'Older and newer guidance may both be in use',
    summary: 'The curator found versions of the same guidance that may not all be needed.',
    why: 'The order alone does not prove which version reflects your current business policy.',
    question: 'Are both versions still correct, or should staff confirm which one is current?',
    choices: [
      { label: 'Both versions are still correct', explanation: 'Keep the saved guidance unchanged and close this question.', resolution: 'not_an_issue' },
      closerReview,
      decideLater,
    ],
  },
  branched_supersession: {
    title: 'One older answer has several possible replacements',
    summary: 'The saved history points to more than one newer answer.',
    why: 'Choosing the current business answer requires human judgment; the curator will not guess.',
    question: 'Should staff confirm which newer answer is the one customers should receive?',
    choices: [closerReview, { label: 'The saved history is correct', explanation: 'Keep all guidance unchanged and close this question.', resolution: 'not_an_issue' }, decideLater],
  },
  literal_dynamic_authority: {
    title: 'A saved price, time, or availability may become outdated',
    summary: 'This guidance contains a value that should come from current Settings or the live calendar.',
    why: 'A fixed saved value can give customers old information. The curator can prepare safer wording, but it cannot approve that wording.',
    question: 'Would you like a safer version prepared for the review queue?',
    choices: [
      { label: 'Prepare safer guidance for review', explanation: 'Add an inactive suggestion to the review queue. The current guidance does not change unless someone approves it separately.', resolution: 'add_safe_replacement_draft' },
      { label: 'The saved value is intentionally fixed', explanation: 'Keep the guidance unchanged and close this question.', resolution: 'not_an_issue' },
      decideLater,
    ],
  },
  exact_duplicate: {
    title: 'The same guidance appears more than once',
    summary: 'Two or more saved items appear to say the same thing for the same customer situation.',
    why: 'They may be intentional, so the curator will not remove or combine anything on its own.',
    question: 'How should your team handle these matching items?',
    choices: [
      { label: 'Prepare one copy for review', explanation: 'Add an inactive combined suggestion for separate staff approval. Existing guidance stays unchanged.', resolution: 'create_consolidation_draft' },
      { label: 'Keep them as separate items', explanation: 'Leave both saved items unchanged and close this question.', resolution: 'keep_both_distinct' },
      closerReview,
      decideLater,
    ],
  },
  invalid_metadata: {
    title: 'Important organising details are missing',
    summary: 'The guidance does not clearly say where or when it should be used.',
    why: 'Without those details, the assistant may not know which customer situation the guidance belongs to.',
    question: 'Would you like staff to review a version with the missing organising details filled in?',
    choices: [
      { label: 'Prepare corrected details for review', explanation: 'Add an inactive suggestion for separate staff approval. Current guidance stays unchanged.', resolution: 'create_metadata_repair_draft' },
      { label: 'The current details are sufficient', explanation: 'Keep the guidance unchanged and close this question.', resolution: 'not_an_issue' },
      closerReview,
      decideLater,
    ],
  },
  shared_provider_specific: {
    title: 'Guidance may belong to only one service line',
    summary: 'Information shared across the business appears to mention details for a particular provider or line.',
    why: 'The curator cannot safely choose the correct line, and will not move the guidance automatically.',
    question: 'Should staff confirm which service line this guidance belongs to?',
    choices: [closerReview, { label: 'Sharing it is correct', explanation: 'Keep the guidance available to both lines and close this question.', resolution: 'not_an_issue' }, decideLater],
  },
  future_record: {
    title: 'Guidance is scheduled for later',
    summary: 'This information has a future start date.',
    why: 'It is being shown so you can confirm the timing is intentional; the curator will not turn it on early.',
    question: 'Is the future start date expected?',
    choices: [{ label: 'Yes, keep it scheduled', explanation: 'Leave the guidance and its timing unchanged.', resolution: 'not_an_issue' }, closerReview, decideLater],
  },
  expired_record: {
    title: 'Guidance may be past its end date',
    summary: 'This information is marked as ended or its saved end date has passed.',
    why: 'The curator will not remove business guidance without a person reviewing it.',
    question: 'Should staff check whether this guidance is still needed?',
    choices: [closerReview, { label: 'The end date is correct', explanation: 'Leave the guidance unchanged and close this question.', resolution: 'not_an_issue' }, decideLater],
  },
  dangling_supersession: {
    title: 'The earlier version can no longer be found',
    summary: 'This guidance refers to an older item that is no longer available.',
    why: 'The curator cannot safely rebuild the missing history or decide what should replace it.',
    question: 'Should staff investigate the missing earlier version?',
    choices: [closerReview, { label: 'No investigation is needed', explanation: 'Keep the available guidance unchanged and close this question.', resolution: 'not_an_issue' }, decideLater],
  },
  cross_topic_supersession: {
    title: 'A newer answer appears linked to a different topic',
    summary: 'The saved connection between an older and newer answer crosses business topics.',
    why: 'That connection could make the wrong guidance look current, so a person needs to confirm it.',
    question: 'Should staff review how these answers are connected?',
    choices: [closerReview, { label: 'The connection is intentional', explanation: 'Keep both answers and their connection unchanged.', resolution: 'not_an_issue' }, decideLater],
  },
  cross_scope_supersession: {
    title: 'A newer answer appears linked to another service line',
    summary: 'The older and newer guidance belong to different parts of the business.',
    why: 'The curator cannot assume that a policy for one service line replaces another line’s policy.',
    question: 'Should staff review which service line should use each answer?',
    choices: [closerReview, { label: 'The connection is intentional', explanation: 'Keep both answers and their connection unchanged.', resolution: 'not_an_issue' }, decideLater],
  },
  cyclic_supersession: {
    title: 'The saved version history loops back on itself',
    summary: 'The guidance does not have a clear newest version.',
    why: 'The curator cannot safely choose a current answer from this history.',
    question: 'Should staff identify the current answer and repair the history?',
    choices: [closerReview, { label: 'Leave the history unchanged', explanation: 'Make no knowledge change and close this question.', resolution: 'not_an_issue' }, decideLater],
  },
};

const fallbackCopy: FindingCopy = {
  title: 'Saved guidance needs a closer look',
  summary: 'The curator found something it cannot safely decide on its own.',
  why: 'A person who understands the business needs to review it. No knowledge will change automatically.',
  question: 'Would you like staff to investigate this guidance?',
  choices: [closerReview, { label: 'No change is needed', explanation: 'Keep the guidance unchanged and close this question.', resolution: 'not_an_issue' }, decideLater],
};

function guidanceText(record: KnowledgeCuratorRecordPreview): string {
  return record.instruction || record.approved_reply || record.knowledge_text || record.example_reply || 'The saved wording is not available. Run a new check before deciding.';
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
  clearing: boolean;
  updatingId: string | null;
  lineLabels: Record<'primary' | 'secondary', string>;
  onRun: () => void;
  onClear: () => void;
  onResolve: (proposal: KnowledgeCuratorProposal, resolution: KnowledgeCuratorResolution, selectedRecordIds?: string[]) => void;
};

export default function KnowledgeCuratorPanel({ state, loadStatus, running, clearing, updatingId, lineLabels, onRun, onClear, onResolve }: Props) {
  const latest = state.runs[0];
  const findings = state.proposals.filter(item => item.status === 'proposed' || item.status === 'accepted');

  return <section className="rounded-xl border border-violet-200 bg-violet-50/40 p-3 sm:p-4" aria-labelledby="knowledge-curator-heading" aria-busy={loadStatus === 'loading' || running}>
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div>
        <h3 id="knowledge-curator-heading" className="text-sm font-bold text-violet-950">Review customer guidance</h3>
        <p className="mt-1 max-w-2xl text-[11px] leading-relaxed text-violet-800">The curator checks for guidance that may be unclear, duplicated, outdated, or in the wrong place. It only raises questions: it never turns on, replaces, or removes customer guidance.</p>
      </div>
      <div className="flex shrink-0 flex-col gap-2">
        <button type="button" onClick={onRun} disabled={running || clearing || loadStatus === 'loading'} aria-label={running ? 'Checking customer guidance' : 'Check customer guidance now'} className="inline-flex min-h-9 items-center justify-center rounded-lg bg-violet-700 px-3 py-2 text-[11px] font-bold text-white hover:bg-violet-800 disabled:opacity-50">
          {running ? 'Checking…' : 'Check guidance now'}
        </button>
        <button type="button" onClick={onClear} disabled={running || clearing || loadStatus !== 'ready'} className="min-h-9 rounded-lg border border-violet-300 bg-white px-3 py-2 text-[11px] font-bold text-violet-900 hover:bg-violet-50 disabled:opacity-50">
          {clearing ? 'Clearing curator questions…' : 'Clear curator questions and start again'}
        </button>
      </div>
    </div>

    {loadStatus === 'loading' && <div role="status" className="mt-3 rounded-lg border border-violet-100 bg-white p-3 text-[11px] text-slate-700">Loading the latest review. No guidance is being changed.</div>}
    {loadStatus === 'error' && <div role="alert" className="mt-3 rounded-lg border border-rose-200 bg-rose-50 p-3 text-[11px] text-rose-900"><strong>The guidance review could not be loaded.</strong><br />Nothing has changed. Check again to retry safely.</div>}

    {loadStatus === 'ready' && <>
      <div className="mt-3 rounded-lg border border-violet-100 bg-white p-3 text-[11px] text-slate-700">
        <strong>{findings.length ? `${findings.length} item${findings.length === 1 ? '' : 's'} waiting for review` : 'No guidance questions are waiting'}</strong>
        <p className="mt-1">{latest ? `Last checked ${new Date(latest.completed_at).toLocaleString()}. ` : 'A check has not run yet. '}{findings.length ? 'Review each item below when you are ready.' : state.automation.enabled ? 'Automatic checks will add a question here if owner input is needed.' : 'Use “Check guidance now” whenever you want to look for issues.'}</p>
        {latest?.status === 'failed' ? <p className="mt-1 text-rose-800">The most recent check could not finish. Nothing changed. Run the check again when you are ready.</p>
          : latest?.error_code ? <p className="mt-1 text-amber-800">The optional writing helper was unavailable, but the safety checks completed. You can try again later.</p>
          : null}
        <p className="mt-1 text-violet-800">Any prepared wording goes to the separate review queue and stays out of customer replies until someone approves it.</p>
      </div>

      {findings.length === 0 ? <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-[11px] text-emerald-900"><strong>Nothing needs your decision right now.</strong><br />Current guidance stays as it is. A future check will show new questions here.</div> : <div className="mt-3 space-y-3">
        {findings.map((proposal, index) => {
          const copy = CURATOR_FINDING_COPY[proposal.finding_type] || fallbackCopy;
          const records = (proposal.record_previews || []).filter(record => record.reference_status === 'current');
          const busy = updatingId === proposal.id;
          const changed = proposal.actionable === false;
          const alreadyPrepared = proposal.status === 'accepted';
          return <article key={proposal.id} className="rounded-lg border border-violet-200 bg-white p-3 sm:p-4" aria-labelledby={`curator-finding-${proposal.id}`}>
            <p className="text-[10px] font-bold uppercase tracking-wide text-violet-700">Review item {index + 1} of {findings.length}</p>
            <h4 id={`curator-finding-${proposal.id}`} className="mt-1 text-sm font-bold text-slate-900">{copy.title}</h4>
            <p className="mt-2 text-[11px] leading-relaxed text-slate-700"><strong>What the curator found:</strong> {copy.summary}</p>
            <p className="mt-1 text-[11px] leading-relaxed text-slate-700"><strong>Affected guidance:</strong> {affectedArea(proposal, lineLabels)}</p>

            {records.length > 0 && <div className="mt-3 grid gap-2 md:grid-cols-2">
              {records.map((record, recordIndex) => <div key={`${record.id}-${record.revision}`} className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-[11px] text-slate-700">
                <p className="font-bold text-slate-900">Saved guidance {records.length > 1 ? recordIndex + 1 : ''}</p>
                {record.applies_when && <p className="mt-1"><strong>Use when:</strong> {record.applies_when}</p>}
                {record.customer_message && <p className="mt-1"><strong>Customer asks:</strong> {record.customer_message}</p>}
                <p className="mt-1 whitespace-pre-wrap"><strong>Guidance:</strong> {guidanceText(record)}</p>
                {proposal.finding_type === 'incompatible_active_records' && !changed && !alreadyPrepared && <>
                  <button type="button" onClick={() => onResolve(proposal, 'select_current_rule', [record.id])} disabled={busy} aria-label={`Choose saved guidance ${recordIndex + 1} for staff review. This records your choice but does not change customer guidance.`} className="mt-3 min-h-9 w-full rounded border border-violet-300 bg-white px-2 py-1.5 font-bold text-violet-900 hover:bg-violet-50 disabled:opacity-50">Choose this answer for staff review</button>
                  <p className="mt-1 text-[10px] text-slate-600">Records your choice only. Customer guidance is not changed.</p>
                </>}
              </div>)}
            </div>}

            <p className="mt-3 text-[11px] leading-relaxed text-slate-700"><strong>Why your input matters:</strong> {copy.why}</p>
            <p className="mt-2 text-xs font-bold text-slate-900">{copy.question}</p>

            {changed ? <div role="alert" className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-[11px] text-amber-900"><strong>This guidance changed after the check.</strong><br />For safety, these choices are unavailable. Run a new check to review the latest wording.</div>
              : alreadyPrepared ? <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-[11px] text-emerald-900"><strong>Safer wording is waiting in the review queue.</strong><br />It is not being used in customer replies. Current guidance remains unchanged until the separate approval step.</div>
              : <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {copy.choices.map(choice => <button key={choice.resolution} type="button" onClick={() => onResolve(proposal, choice.resolution)} disabled={busy} aria-label={`${choice.label}. ${choice.explanation}`} className="min-h-12 rounded-lg border border-slate-300 bg-white px-3 py-2 text-left text-[11px] hover:border-violet-400 hover:bg-violet-50 disabled:opacity-50">
                  <span className="block font-bold text-slate-900">{busy ? 'Saving your choice…' : choice.label}</span>
                  <span className="mt-0.5 block leading-relaxed text-slate-600">{choice.explanation}</span>
                </button>)}
              </div>}
          </article>;
        })}
      </div>}
    </>}
  </section>;
}
