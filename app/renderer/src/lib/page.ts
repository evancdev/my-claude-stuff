// Ids, not objects, so the open page survives the plans being re-read.
export type Page = { section: string; repo?: string; plan?: string };

export function repoName(label: string) {
  return label.split("/").filter(Boolean).pop() || label;
}
