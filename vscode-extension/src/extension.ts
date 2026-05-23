import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import * as crypto from 'crypto';

interface SerializedComment {
  author: string;
  body: string;
  timestamp: string;
}

interface Annotation {
  id: string;
  anchor: {
    line_start: number;
    line_end: number;
    char_start: number;
    char_end: number;
  };
  quote: string;
  resolved: boolean;
  comments: SerializedComment[];
}

interface SidecarFile {
  annotations: Annotation[];
}

const USER_AUTHOR = 'You';
type AnnotationStore = Map<string, Annotation[]>;

function newId(): string {
  return crypto.randomBytes(3).toString('hex');
}

function nowIso(): string {
  return new Date().toISOString();
}

function isAnnotatable(uri: vscode.Uri): boolean {
  if (uri.scheme !== 'file') return false;
  if (/\.annotations\.json$/.test(uri.fsPath)) return false;
  return true;
}

function sidecarPathFor(srcPath: string): string {
  return `${srcPath}.annotations.json`;
}

function sourcePathForSidecar(sidecarPath: string): string {
  return sidecarPath.replace(/\.annotations\.json$/, '');
}

function indexKey(uri: vscode.Uri): string {
  return uri.toString();
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function coerceAnchorField(v: unknown, fallback: number, migratedFlag: { v: boolean }): number {
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  migratedFlag.v = true;
  return fallback;
}

function parseAnnotation(raw: unknown, migratedFlag: { v: boolean }): Annotation | null {
  if (!isRecord(raw)) return null;
  const anchor = raw.anchor;
  if (!isRecord(anchor)) return null;
  const idVal = raw.id;
  const id = typeof idVal === 'string' ? idVal : ((migratedFlag.v = true), newId());
  const resolvedVal = raw.resolved;
  const resolved = typeof resolvedVal === 'boolean' ? resolvedVal : ((migratedFlag.v = true), false);
  const comments = Array.isArray(raw.comments)
    ? raw.comments.filter(isRecord).map((c) => ({
        author: typeof c.author === 'string' ? c.author : USER_AUTHOR,
        body: typeof c.body === 'string' ? c.body : '',
        timestamp: typeof c.timestamp === 'string' ? c.timestamp : nowIso(),
      }))
    : [];
  return {
    id,
    anchor: {
      line_start: coerceAnchorField(anchor.line_start, 1, migratedFlag),
      line_end: coerceAnchorField(anchor.line_end, 1, migratedFlag),
      char_start: coerceAnchorField(anchor.char_start, 0, migratedFlag),
      char_end: coerceAnchorField(anchor.char_end, 0, migratedFlag),
    },
    quote: typeof raw.quote === 'string' ? raw.quote : '',
    resolved,
    comments,
  };
}

// Unknown top-level keys captured at load, replayed on write. Avoids a per-write disk re-read.
const extrasCache = new Map<string, Record<string, unknown>>();

function loadSidecar(srcPath: string): { annotations: Annotation[]; migrated: boolean } {
  const p = sidecarPathFor(srcPath);
  if (!fs.existsSync(p)) {
    extrasCache.delete(p);
    return { annotations: [], migrated: false };
  }
  try {
    const raw = fs.readFileSync(p, 'utf8');
    const parsed: unknown = JSON.parse(raw);
    const extras: Record<string, unknown> = {};
    if (isRecord(parsed)) {
      for (const [k, v] of Object.entries(parsed)) {
        if (k !== 'annotations') extras[k] = v;
      }
    }
    extrasCache.set(p, extras);
    const annsField: unknown = isRecord(parsed) ? parsed.annotations : undefined;
    const raws: unknown[] = Array.isArray(annsField) ? annsField : [];
    const migratedFlag = { v: false };
    const annotations: Annotation[] = [];
    for (const r of raws) {
      const ann = parseAnnotation(r, migratedFlag);
      if (ann) annotations.push(ann);
    }
    return { annotations, migrated: migratedFlag.v };
  } catch (err) {
    vscode.window.showErrorMessage(`Claude Annotate: failed to parse ${path.basename(p)}: ${err}`);
    return { annotations: [], migrated: false };
  }
}

function writeSidecar(srcPath: string, annotations: Annotation[]) {
  const p = sidecarPathFor(srcPath);
  fs.mkdirSync(path.dirname(p), { recursive: true });
  const extras = extrasCache.get(p) ?? {};
  const sorted = [...annotations].sort(
    (a, b) =>
      a.anchor.line_start - b.anchor.line_start ||
      a.anchor.char_start - b.anchor.char_start,
  );
  const payload = { ...extras, annotations: sorted };
  const content = JSON.stringify(payload, null, 2) + '\n';
  // Atomic write: tmp file in same directory + rename. Mirrors the Python harness so
  // concurrent writers can't truncate each other.
  const tmp = `${p}.${process.pid}.${Date.now()}.tmp`;
  try {
    fs.writeFileSync(tmp, content, 'utf8');
    fs.renameSync(tmp, p);
  } catch (err) {
    try {
      fs.unlinkSync(tmp);
    } catch {
      // Already gone — best effort cleanup.
    }
    throw err;
  }
}

function annotationToRange(ann: Annotation, doc: vscode.TextDocument): vscode.Range {
  const startLine = Math.max(0, Math.min(doc.lineCount - 1, ann.anchor.line_start - 1));
  const endLine = Math.max(0, Math.min(doc.lineCount - 1, ann.anchor.line_end - 1));
  const startChar = Math.max(0, ann.anchor.char_start ?? 0);
  const endLineText = doc.lineAt(endLine).text;
  const endChar = Math.max(0, Math.min(endLineText.length, ann.anchor.char_end ?? endLineText.length));
  return new vscode.Range(startLine, startChar, endLine, endChar);
}

function formatTimestamp(ts: string): string {
  const d = new Date(ts);
  if (!Number.isFinite(d.getTime())) return ts;
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function escapeHtml(s: string): string {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function lensLabel(ann: Annotation): string {
  const count = ann.comments.length;
  return count === 1 ? '1 comment' : `${count} comments`;
}

class AnnotationLensProvider implements vscode.CodeLensProvider {
  private emitter = new vscode.EventEmitter<void>();
  onDidChangeCodeLenses = this.emitter.event;

  constructor(private store: AnnotationStore) {}

  refresh() {
    this.emitter.fire();
  }

  provideCodeLenses(doc: vscode.TextDocument): vscode.ProviderResult<vscode.CodeLens[]> {
    if (!isAnnotatable(doc.uri)) return [];
    const annotations = this.store.get(indexKey(doc.uri)) ?? [];
    const sidecar = sidecarPathFor(doc.uri.fsPath);
    return annotations.map((ann) => {
      const lineIndex = Math.max(0, Math.min(doc.lineCount - 1, ann.anchor.line_start - 1));
      const lensRange = new vscode.Range(lineIndex, 0, lineIndex, 0);
      return new vscode.CodeLens(lensRange, {
        title: lensLabel(ann),
        command: 'claudeAnnotate.showThread',
        arguments: [ann.id, sidecar],
        tooltip: 'Open annotation thread',
      });
    });
  }
}

function buildWebviewHtml(ann: Annotation, nonce: string): string {
  const resolveLabel = ann.resolved ? 'Unresolve' : 'Resolve';
  const commentsHtml = ann.comments
    .map((c) => {
      const claudeClass = c.author === 'Claude' ? 'claude' : 'user';
      return `
      <div class="comment ${claudeClass}">
        <div class="meta">
          <span class="author">${escapeHtml(c.author)}</span>
          <span class="timestamp">${escapeHtml(formatTimestamp(c.timestamp))}</span>
        </div>
        <div class="body">${escapeHtml(c.body)}</div>
      </div>`;
    })
    .join('\n');

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';" />
  <title>Claude Annotate</title>
  <style>
    body {
      font-family: var(--vscode-font-family);
      color: var(--vscode-foreground);
      background: var(--vscode-editor-background);
      padding: 16px;
      font-size: var(--vscode-font-size);
      line-height: 1.5;
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      margin-bottom: 8px;
      font-size: 0.85em;
      color: var(--vscode-descriptionForeground);
    }
    .header .id {
      font-family: var(--vscode-editor-font-family, monospace);
    }
    .quote {
      border-left: 2px solid var(--vscode-textBlockQuote-border);
      background: var(--vscode-textBlockQuote-background);
      padding: 6px 10px;
      margin: 0 0 12px 0;
      font-family: var(--vscode-editor-font-family, monospace);
      font-size: 0.9em;
      white-space: pre-wrap;
      color: var(--vscode-descriptionForeground);
    }
    .comments { margin: 0 0 12px 0; }
    .comment {
      padding: 8px 0;
      border-top: 1px solid var(--vscode-panel-border);
    }
    .comment:first-child { border-top: none; }
    .meta { font-size: 0.85em; margin-bottom: 4px; }
    .author { font-weight: 600; }
    .comment.claude .author { color: var(--vscode-charts-orange, #ff9800); }
    .timestamp { color: var(--vscode-descriptionForeground); margin-left: 6px; }
    .body { white-space: pre-wrap; word-wrap: break-word; }
    .actions {
      display: flex;
      gap: 8px;
      margin-top: 12px;
      padding-top: 12px;
      border-top: 1px solid var(--vscode-panel-border);
    }
    button {
      background: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
      border: none;
      padding: 6px 12px;
      font-family: inherit;
      font-size: inherit;
      cursor: pointer;
      border-radius: 2px;
    }
    button:hover { background: var(--vscode-button-hoverBackground); }
    button.secondary {
      background: var(--vscode-button-secondaryBackground, transparent);
      color: var(--vscode-button-secondaryForeground, var(--vscode-foreground));
      border: 1px solid var(--vscode-panel-border);
    }
    button.secondary:hover {
      background: var(--vscode-button-secondaryHoverBackground, var(--vscode-list-hoverBackground));
    }
    button.danger {
      background: transparent;
      color: var(--vscode-errorForeground, #e74c3c);
      border: 1px solid var(--vscode-errorForeground, #e74c3c);
    }
    button.danger:hover {
      background: var(--vscode-inputValidation-errorBackground, rgba(231, 76, 60, 0.1));
    }
  </style>
</head>
<body>
  <div class="header">
    <span class="id">${escapeHtml(ann.id)}</span>
  </div>
  ${ann.quote ? `<div class="quote">${escapeHtml(ann.quote)}</div>` : ''}
  <div class="comments">${commentsHtml}</div>
  <div class="actions">
    <button data-cmd="reply">Reply</button>
    <button class="secondary" data-cmd="toggleResolved">${escapeHtml(resolveLabel)}</button>
    <button class="danger" data-cmd="delete">Delete</button>
  </div>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    document.querySelectorAll('button[data-cmd]').forEach((btn) => {
      btn.addEventListener('click', () => {
        vscode.postMessage({ cmd: btn.getAttribute('data-cmd') });
      });
    });
  </script>
</body>
</html>`;
}

interface PanelState {
  panel: vscode.WebviewPanel;
  annotationId: string;
  sidecarPath: string;
}

// Exported for unit testing — these are pure helpers with no UI dependencies.
export {
  newId,
  nowIso,
  isAnnotatable,
  sidecarPathFor,
  sourcePathForSidecar,
  isRecord,
  parseAnnotation,
  loadSidecar,
  writeSidecar,
  formatTimestamp,
  escapeHtml,
  lensLabel,
  buildWebviewHtml,
};
export type { Annotation, SerializedComment, SidecarFile };

export function activate(context: vscode.ExtensionContext) {
  const store: AnnotationStore = new Map();
  let panelState: PanelState | null = null;

  // Three decoration types: open-user (yellow), open-claude (orange), resolved (green).
  const userDecoration = vscode.window.createTextEditorDecorationType({
    backgroundColor: 'rgba(255, 213, 79, 0.35)',
    borderRadius: '2px',
    overviewRulerColor: 'rgba(255, 193, 7, 0.8)',
    overviewRulerLane: vscode.OverviewRulerLane.Right,
  });
  const claudeDecoration = vscode.window.createTextEditorDecorationType({
    backgroundColor: 'rgba(255, 152, 0, 0.30)',
    borderRadius: '2px',
    overviewRulerColor: 'rgba(255, 109, 0, 0.8)',
    overviewRulerLane: vscode.OverviewRulerLane.Right,
  });
  const resolvedDecoration = vscode.window.createTextEditorDecorationType({
    backgroundColor: 'rgba(76, 175, 80, 0.28)',
    borderRadius: '2px',
    overviewRulerColor: 'rgba(56, 142, 60, 0.8)',
    overviewRulerLane: vscode.OverviewRulerLane.Right,
  });
  context.subscriptions.push(userDecoration, claudeDecoration, resolvedDecoration);

  const lensProvider = new AnnotationLensProvider(store);
  context.subscriptions.push(
    vscode.languages.registerCodeLensProvider({ scheme: 'file' }, lensProvider),
  );

  function refreshDecorations(editor: vscode.TextEditor | undefined) {
    if (!editor) return;
    if (!isAnnotatable(editor.document.uri)) {
      editor.setDecorations(userDecoration, []);
      editor.setDecorations(claudeDecoration, []);
      editor.setDecorations(resolvedDecoration, []);
      return;
    }
    const annotations = store.get(indexKey(editor.document.uri)) ?? [];
    const userRanges: vscode.Range[] = [];
    const claudeRanges: vscode.Range[] = [];
    const resolvedRanges: vscode.Range[] = [];
    for (const ann of annotations) {
      const range = annotationToRange(ann, editor.document);
      if (ann.resolved) {
        resolvedRanges.push(range);
      } else if (ann.comments[0]?.author === 'Claude') {
        claudeRanges.push(range);
      } else {
        userRanges.push(range);
      }
    }
    editor.setDecorations(userDecoration, userRanges);
    editor.setDecorations(claudeDecoration, claudeRanges);
    editor.setDecorations(resolvedDecoration, resolvedRanges);
  }

  function refreshAllEditorsFor(uri: vscode.Uri) {
    const target = uri.toString();
    for (const editor of vscode.window.visibleTextEditors) {
      if (editor.document.uri.toString() === target) refreshDecorations(editor);
    }
  }

  function hydrate(doc: vscode.TextDocument) {
    if (!isAnnotatable(doc.uri)) return;
    const { annotations, migrated } = loadSidecar(doc.uri.fsPath);
    store.set(indexKey(doc.uri), annotations);
    if (migrated) writeSidecar(doc.uri.fsPath, annotations);
    lensProvider.refresh();
    refreshAllEditorsFor(doc.uri);
  }

  // Hydrate already-open docs.
  vscode.workspace.textDocuments.forEach(hydrate);
  context.subscriptions.push(vscode.workspace.onDidOpenTextDocument(hydrate));

  // External sidecar changes (e.g. harness / Claude write) → re-load.
  const watcher = vscode.workspace.createFileSystemWatcher('**/*.annotations.json');
  const reHydrateForSidecar = (sidecarUri: vscode.Uri) => {
    const srcPath = sourcePathForSidecar(sidecarUri.fsPath);
    const srcUri = vscode.Uri.file(srcPath);
    const doc = vscode.workspace.textDocuments.find((d) => d.uri.toString() === srcUri.toString());
    if (doc) {
      hydrate(doc);
    } else {
      // Doc not open; just clear cached store entry so next open re-reads.
      store.delete(srcUri.toString());
    }
    // If the open thread panel is showing an annotation from this sidecar, re-render it.
    if (panelState && panelState.sidecarPath === sidecarPathFor(srcPath)) {
      renderPanelForCurrent();
    }
  };
  context.subscriptions.push(watcher);
  context.subscriptions.push(watcher.onDidChange(reHydrateForSidecar));
  context.subscriptions.push(watcher.onDidCreate(reHydrateForSidecar));

  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor((editor) => refreshDecorations(editor)),
  );

  function findAnnotation(sidecarPath: string, id: string): Annotation | undefined {
    const srcUri = vscode.Uri.file(sourcePathForSidecar(sidecarPath));
    const annotations = store.get(srcUri.toString());
    return annotations?.find((a) => a.id === id);
  }

  function mutateAnnotation(
    sidecarPath: string,
    id: string,
    mutator: (a: Annotation) => boolean | void,
  ): Annotation | undefined {
    const srcPath = sourcePathForSidecar(sidecarPath);
    const srcUri = vscode.Uri.file(srcPath);
    const list = store.get(srcUri.toString());
    if (!list) return undefined;
    const ann = list.find((a) => a.id === id);
    if (!ann) return undefined;
    const result = mutator(ann);
    if (result === false) return ann; // mutator opts out of persisting
    writeSidecar(srcPath, list);
    lensProvider.refresh();
    refreshAllEditorsFor(srcUri);
    return ann;
  }

  function renderPanelForCurrent() {
    if (!panelState) return;
    const ann = findAnnotation(panelState.sidecarPath, panelState.annotationId);
    if (!ann) {
      panelState.panel.dispose();
      return;
    }
    const nonce = crypto.randomBytes(16).toString('base64');
    panelState.panel.webview.html = buildWebviewHtml(ann, nonce);
  }

  function openOrFocusPanel(id: string, sidecarPath: string) {
    if (panelState) {
      panelState.annotationId = id;
      panelState.sidecarPath = sidecarPath;
      panelState.panel.reveal(vscode.ViewColumn.Beside, true);
      renderPanelForCurrent();
      return;
    }
    const panel = vscode.window.createWebviewPanel(
      'claudeAnnotate.thread',
      'Annotation',
      { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
      { enableScripts: true, retainContextWhenHidden: true },
    );
    panelState = { panel, annotationId: id, sidecarPath };
    panel.onDidDispose(() => {
      panelState = null;
    });
    panel.webview.onDidReceiveMessage((msg) => {
      if (!panelState) return;
      const { annotationId, sidecarPath } = panelState;
      switch (msg?.cmd) {
        case 'reply':
          replyById(annotationId, sidecarPath);
          break;
        case 'toggleResolved':
          toggleResolvedById(annotationId, sidecarPath);
          break;
        case 'delete':
          deleteById(annotationId, sidecarPath);
          break;
      }
    });
    renderPanelForCurrent();
  }

  async function replyById(id: string, sidecarPath: string) {
    const ann = findAnnotation(sidecarPath, id);
    if (!ann) return;
    const body = await vscode.window.showInputBox({
      prompt: 'Reply to annotation',
      placeHolder: 'Type your reply',
      ignoreFocusOut: true,
    });
    if (!body || !body.trim()) return;
    mutateAnnotation(sidecarPath, id, (a) => {
      a.comments.push({ author: USER_AUTHOR, body, timestamp: nowIso() });
    });
    renderPanelForCurrent();
  }

  function toggleResolvedById(id: string, sidecarPath: string) {
    mutateAnnotation(sidecarPath, id, (a) => {
      a.resolved = !a.resolved;
    });
    renderPanelForCurrent();
  }

  async function deleteById(id: string, sidecarPath: string) {
    const confirm = await vscode.window.showWarningMessage(
      'Delete this annotation? This cannot be undone.',
      { modal: true },
      'Delete',
    );
    if (confirm !== 'Delete') return;
    const srcPath = sourcePathForSidecar(sidecarPath);
    const srcUri = vscode.Uri.file(srcPath);
    const list = store.get(srcUri.toString());
    if (!list) return;
    const idx = list.findIndex((a) => a.id === id);
    if (idx < 0) return;
    list.splice(idx, 1);
    writeSidecar(srcPath, list);
    lensProvider.refresh();
    refreshAllEditorsFor(srcUri);
    if (panelState?.annotationId === id) {
      panelState.panel.dispose();
    }
  }

  context.subscriptions.push(
    vscode.commands.registerCommand('claudeAnnotate.showThread', (id: string, sidecarPath: string) => {
      if (!id || !sidecarPath) return;
      openOrFocusPanel(id, sidecarPath);
    }),
    vscode.commands.registerCommand('claudeAnnotate.replyById', (id: string, sidecarPath: string) => {
      if (!id || !sidecarPath) return;
      void replyById(id, sidecarPath);
    }),
    vscode.commands.registerCommand('claudeAnnotate.toggleResolvedById', (id: string, sidecarPath: string) => {
      if (!id || !sidecarPath) return;
      toggleResolvedById(id, sidecarPath);
    }),
    vscode.commands.registerCommand('claudeAnnotate.deleteById', (id: string, sidecarPath: string) => {
      if (!id || !sidecarPath) return;
      void deleteById(id, sidecarPath);
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('claudeAnnotate.addAtSelection', async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;
      if (!isAnnotatable(editor.document.uri)) {
        vscode.window.showInformationMessage('Claude Annotate: this file type is not annotatable.');
        return;
      }
      const selection = editor.selection;
      const range = selection.isEmpty
        ? editor.document.lineAt(selection.active.line).range
        : new vscode.Range(selection.start, selection.end);
      const quote = editor.document.getText(range);
      const body = await vscode.window.showInputBox({
        prompt: 'New annotation',
        placeHolder: 'Type your comment',
        ignoreFocusOut: true,
      });
      if (!body || !body.trim()) return;
      const ann: Annotation = {
        id: newId(),
        anchor: {
          line_start: range.start.line + 1,
          line_end: range.end.line + 1,
          char_start: range.start.character,
          char_end: range.end.character,
        },
        quote,
        resolved: false,
        comments: [{ author: USER_AUTHOR, body, timestamp: nowIso() }],
      };
      const srcPath = editor.document.uri.fsPath;
      const key = indexKey(editor.document.uri);
      const list = store.get(key) ?? [];
      list.push(ann);
      store.set(key, list);
      writeSidecar(srcPath, list);
      lensProvider.refresh();
      refreshAllEditorsFor(editor.document.uri);
    }),
  );
}

export function deactivate() {
  // Disposables registered via context.subscriptions handle cleanup.
}
