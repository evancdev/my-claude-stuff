// Minimal `vscode` API stub for unit testing.
// Only the surface the extension actually uses at module load + test time.

export class Position {
  constructor(public line: number, public character: number) {}
}

export class Range {
  constructor(public start: Position, public end: Position) {}
}

export class Uri {
  constructor(public scheme: string, public fsPath: string) {}
  static file(p: string): Uri {
    return new Uri('file', p);
  }
  toString(): string {
    return `${this.scheme}://${this.fsPath}`;
  }
}

export class CodeLens {
  constructor(public range: Range, public command?: unknown) {}
}

export const EventEmitter = class<T> {
  event = (_listener: (e: T) => void) => ({ dispose() {} });
  fire(_e: T) {}
  dispose() {}
};

export const commands = {
  registerCommand: (_id: string, _cb: unknown) => ({ dispose() {} }),
  executeCommand: async (_id: string, ..._args: unknown[]) => undefined,
};

export const window = {
  activeTextEditor: undefined as unknown,
  showErrorMessage: (_msg: string) => undefined,
  showInformationMessage: (_msg: string) => undefined,
  showInputBox: async (_opts?: unknown) => undefined,
  createWebviewPanel: () => ({ webview: { html: '', onDidReceiveMessage: () => ({ dispose() {} }) }, dispose() {} }),
  onDidChangeActiveTextEditor: (_listener: unknown) => ({ dispose() {} }),
};

export const workspace = {
  getConfiguration: (_section?: string) => ({ get: (_k: string) => undefined }),
  onDidSaveTextDocument: (_listener: unknown) => ({ dispose() {} }),
  onDidChangeTextDocument: (_listener: unknown) => ({ dispose() {} }),
  onDidOpenTextDocument: (_listener: unknown) => ({ dispose() {} }),
  textDocuments: [] as unknown[],
};

export const languages = {
  registerCodeLensProvider: (_selector: unknown, _provider: unknown) => ({ dispose() {} }),
};

export const ViewColumn = { Beside: 2, Active: -1 };

export const ConfigurationTarget = { Global: 1, Workspace: 2, WorkspaceFolder: 3 };
