export type VariableInsertTarget = {
  element: HTMLInputElement | HTMLTextAreaElement;
  apply: (value: string) => void;
  selectionStart: number;
  selectionEnd: number;
};

export function applyVariableTokenAtSelection(target: VariableInsertTarget, token: string): number {
  const { element, apply } = target;
  const start = target.selectionStart;
  const end = target.selectionEnd;
  const inserted = `{${token}}`;

  apply(`${element.value.slice(0, start)}${inserted}${element.value.slice(end)}`);
  const nextCaret = start + inserted.length;
  target.selectionStart = nextCaret;
  target.selectionEnd = nextCaret;
  return nextCaret;
}
