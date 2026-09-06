export const NATURAL_EXTRA = {
  id: 'natural' as const,
  name: 'Natural',
  price: 100,
};

export function bookingTotal(servicePrice: number, selectedExtraIds: readonly string[]): number {
  return servicePrice + (selectedExtraIds.includes(NATURAL_EXTRA.id) ? NATURAL_EXTRA.price : 0);
}
