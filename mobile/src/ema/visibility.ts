import { EMAAnswerValue, EMAItem, EMASubItem } from '../api/types';

export function isSubItemVisible(
  sub: EMASubItem,
  answers: Record<string, EMAAnswerValue>,
): boolean {
  const dep = sub.depends_on;
  if (!dep) return true;
  const parent = answers[dep.sub_item_id];
  if (parent === undefined) return false;
  if (dep.equals !== undefined) return parent === dep.equals;
  if (dep.not_equals !== undefined) return parent !== dep.not_equals;
  return true;
}

export function visibleSubItems(
  items: EMAItem[],
  answers: Record<string, EMAAnswerValue>,
): EMASubItem[] {
  return items.flatMap((item) =>
    (item.sub_items ?? []).filter((sub) => isSubItemVisible(sub, answers)),
  );
}

export function isAnswered(value: EMAAnswerValue | undefined): boolean {
  if (value === undefined) return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'string') return value.length > 0;
  return true;
}

export function pruneHiddenAnswers(
  items: EMAItem[],
  answers: Record<string, EMAAnswerValue>,
): Record<string, EMAAnswerValue> {
  const next = { ...answers };
  for (const item of items) {
    for (const sub of item.sub_items ?? []) {
      if (!isSubItemVisible(sub, next)) {
        delete next[sub.sub_item_id];
      }
    }
  }
  return next;
}
