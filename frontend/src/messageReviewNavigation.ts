export interface MessageReviewNavigation {
  previousIndex: number | null;
  nextIndex: number | null;
  position: number | null;
  total: number;
}

/** Resolve navigation in the order supplied by the active inbox filters. */
export function getMessageReviewNavigation(
  messageIds: string[],
  selectedMessageId: string | null,
  anchorIndex = 0,
): MessageReviewNavigation {
  const total = messageIds.length;
  if (total === 0) {
    return { previousIndex: null, nextIndex: null, position: null, total };
  }

  const selectedIndex = selectedMessageId === null ? -1 : messageIds.indexOf(selectedMessageId);
  if (selectedIndex >= 0) {
    return {
      previousIndex: selectedIndex > 0 ? selectedIndex - 1 : null,
      nextIndex: selectedIndex + 1 < total ? selectedIndex + 1 : null,
      position: selectedIndex + 1,
      total,
    };
  }

  // A cleared item can disappear under the active filter while its detail is
  // still open. Its former index remains the boundary between previous/next.
  const insertionIndex = Math.max(0, Math.min(anchorIndex, total));
  return {
    previousIndex: insertionIndex > 0 ? insertionIndex - 1 : null,
    nextIndex: insertionIndex < total ? insertionIndex : null,
    position: null,
    total,
  };
}
