export type VariableInsertTarget = {
  element: HTMLInputElement | HTMLTextAreaElement;
  apply: (value: string) => void;
};

export function applyVariableTokenAtSelection(target: VariableInsertTarget, token: string): number {
  const { element, apply } = target;
  const start = element.selectionStart ?? element.value.length;
  const end = element.selectionEnd ?? start;
  const inserted = `{${token}}`;

  apply(`${element.value.slice(0, start)}${inserted}${element.value.slice(end)}`);
  return start + inserted.length;
}
