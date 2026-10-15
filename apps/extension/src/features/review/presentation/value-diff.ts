/**
 * 功能：生成字符串差异片段。
 * 职责边界：只标记显示差异，不标准化业务字段。
 * 修改日期：2026-08-26
 * 修改人：wuyi
 */

export interface DiffPart {
  text: string;
  changed: boolean;
}

/** Marks only characters that are not shared by both compared values. */
export function diffValue(value: string | number | null | undefined, other: string | number | null | undefined): DiffPart[] {
  const left = String(value ?? "");
  const right = String(other ?? "");
  if (!left || !right || left === right) return [{ text: left, changed: false }];
  const table = Array.from({ length: left.length + 1 }, () => Array<number>(right.length + 1).fill(0));
  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      table[i][j] = left[i] === right[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const parts: DiffPart[] = [];
  let i = 0;
  let j = 0;
  while (i < left.length) {
    if (j < right.length && left[i] === right[j]) {
      parts.push({ text: left[i], changed: false });
      i += 1;
      j += 1;
    } else if (j >= right.length || table[i + 1][j] >= table[i][j + 1]) {
      parts.push({ text: left[i], changed: true });
      i += 1;
    } else {
      j += 1;
    }
  }
  return parts.reduce<DiffPart[]>((result, part) => {
    const previous = result.at(-1);
    if (previous?.changed === part.changed) previous.text += part.text;
    else result.push({ ...part });
    return result;
  }, []);
}

/** Marks differences at the same character position without realigning repeats. */
export function diffValueByPosition(
  value: string | number | null | undefined,
  other: string | number | null | undefined,
): DiffPart[] {
  const left = String(value ?? "");
  const right = String(other ?? "");
  if (!left || !right || left === right) return [{ text: left, changed: false }];

  return [...left].reduce<DiffPart[]>((result, character, index) => {
    const changed = character !== right[index];
    const previous = result.at(-1);
    if (previous?.changed === changed) previous.text += character;
    else result.push({ text: character, changed });
    return result;
  }, []);
}
