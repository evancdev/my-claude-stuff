/**
 * Tests for the Claude Annotate VS Code extension.
 *
 * Organized by subject (parsing, loading, writing, rendering, interop), not
 * by methodology. Two access modes:
 *
 *  - **Unit** — exercise the extension's pure helpers directly (exported from
 *    `src/extension.ts`). Fast, no subprocess. The `vscode` module is mocked
 *    via a vitest alias (see `vitest.config.ts`).
 *  - **Interop** — invoke the Python CLI (`scripts/annotate.py`) as a
 *    subprocess and assert the TS extension's loader/writer agrees with it
 *    on the sidecar JSON contract. The Python CLI must be on PATH as
 *    `python3` for these tests.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { execFileSync } from 'node:child_process';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { Uri } from './__mocks__/vscode';

import {
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
  type Annotation,
} from '../src/extension';

// -----------------------------------------------------------------------------
// Temp dir helper
// -----------------------------------------------------------------------------

let tmp: string;
beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wb-annotate-'));
});
afterEach(() => {
  fs.rmSync(tmp, { recursive: true, force: true });
});

function srcPathIn(name = 'file.ts'): string {
  return path.join(tmp, name);
}

function makeAnn(overrides: Partial<Annotation> = {}): Annotation {
  return {
    id: 'abc123',
    anchor: { line_start: 1, line_end: 1, char_start: 0, char_end: 5 },
    quote: 'hello',
    resolved: false,
    comments: [{ author: 'You', body: 'a comment', timestamp: '2025-01-01T00:00:00.000Z' }],
    ...overrides,
  };
}

// -----------------------------------------------------------------------------
// newId
// -----------------------------------------------------------------------------

describe('newId', () => {
  it('returns 6 lowercase hex chars', () => {
    const id = newId();
    expect(id).toMatch(/^[0-9a-f]{6}$/);
  });

  it('produces unique ids across many calls (no collisions in 1000)', () => {
    const seen = new Set<string>();
    for (let i = 0; i < 1000; i++) seen.add(newId());
    expect(seen.size).toBe(1000);
  });
});

// -----------------------------------------------------------------------------
// nowIso
// -----------------------------------------------------------------------------

describe('nowIso', () => {
  it('returns a parseable ISO 8601 string with Z suffix', () => {
    const s = nowIso();
    expect(s).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
    expect(Number.isNaN(Date.parse(s))).toBe(false);
  });
});

// -----------------------------------------------------------------------------
// isRecord
// -----------------------------------------------------------------------------

describe('isRecord', () => {
  it('returns true for plain objects', () => {
    expect(isRecord({})).toBe(true);
    expect(isRecord({ a: 1 })).toBe(true);
  });

  it('returns false for null', () => {
    expect(isRecord(null)).toBe(false);
  });

  it('returns false for primitives', () => {
    expect(isRecord(0)).toBe(false);
    expect(isRecord('')).toBe(false);
    expect(isRecord('abc')).toBe(false);
    expect(isRecord(true)).toBe(false);
    expect(isRecord(undefined)).toBe(false);
  });

  // BUG candidate: isRecord returns true for arrays (typeof [] === 'object').
  // The name implies "plain record", but arrays slip through.
  it('returns false for arrays (a record should not be an array)', () => {
    expect(isRecord([])).toBe(false);
    expect(isRecord([1, 2, 3])).toBe(false);
  });

  it('returns true for class instances (any non-null object)', () => {
    class Foo {}
    expect(isRecord(new Foo())).toBe(true);
  });
});

// -----------------------------------------------------------------------------
// isAnnotatable
// -----------------------------------------------------------------------------

describe('isAnnotatable', () => {
  it('accepts a regular file:// uri', () => {
    expect(isAnnotatable(Uri.file('/tmp/foo.ts') as any)).toBe(true);
  });

  it('rejects non-file schemes', () => {
    expect(isAnnotatable(new Uri('untitled', '/tmp/foo.ts') as any)).toBe(false);
    expect(isAnnotatable(new Uri('git', '/tmp/foo.ts') as any)).toBe(false);
    expect(isAnnotatable(new Uri('output', '/tmp/foo.ts') as any)).toBe(false);
  });

  it('rejects *.annotations.json files', () => {
    expect(isAnnotatable(Uri.file('/tmp/foo.ts.annotations.json') as any)).toBe(false);
    expect(isAnnotatable(Uri.file('/tmp/data.annotations.json') as any)).toBe(false);
  });

  it('accepts files where .annotations.json appears in the middle of the path', () => {
    expect(isAnnotatable(Uri.file('/tmp/foo.annotations.json.bak') as any)).toBe(true);
  });
});

// -----------------------------------------------------------------------------
// sidecarPathFor / sourcePathForSidecar (round-trip)
// -----------------------------------------------------------------------------

describe('sidecarPathFor / sourcePathForSidecar', () => {
  it.each([
    '/tmp/foo.ts',
    '/tmp/foo',
    '/tmp/a.b.c',
    '/tmp/foo.annotations.json.bak',
    'relative/path.txt',
    '/',
  ])('round-trips %s through sidecarPathFor → sourcePathForSidecar', (p) => {
    expect(sourcePathForSidecar(sidecarPathFor(p))).toBe(p);
  });

  it('appends .annotations.json once', () => {
    expect(sidecarPathFor('/tmp/foo.ts')).toBe('/tmp/foo.ts.annotations.json');
  });

  it('only strips .annotations.json suffix at end', () => {
    expect(sourcePathForSidecar('/tmp/foo.annotations.json.bak')).toBe(
      '/tmp/foo.annotations.json.bak',
    );
  });

  it('handles empty path', () => {
    expect(sidecarPathFor('')).toBe('.annotations.json');
    expect(sourcePathForSidecar('.annotations.json')).toBe('');
  });
});

// -----------------------------------------------------------------------------
// parseAnnotation
// -----------------------------------------------------------------------------

describe('parseAnnotation', () => {
  function flag() {
    return { v: false };
  }

  it('parses a fully valid annotation', () => {
    const f = flag();
    const raw = {
      id: 'xyz789',
      anchor: { line_start: 3, line_end: 5, char_start: 2, char_end: 10 },
      quote: 'q',
      resolved: true,
      comments: [{ author: 'Claude', body: 'b', timestamp: '2024-01-01T00:00:00.000Z' }],
    };
    const ann = parseAnnotation(raw, f);
    expect(ann).toEqual(raw);
    expect(f.v).toBe(false);
  });

  it('returns null when raw is not an object', () => {
    expect(parseAnnotation(null, flag())).toBeNull();
    expect(parseAnnotation('str', flag())).toBeNull();
    expect(parseAnnotation(42, flag())).toBeNull();
    expect(parseAnnotation(undefined, flag())).toBeNull();
  });

  it('returns null when anchor is missing or not an object', () => {
    expect(parseAnnotation({}, flag())).toBeNull();
    expect(parseAnnotation({ anchor: null }, flag())).toBeNull();
    expect(parseAnnotation({ anchor: 'no' }, flag())).toBeNull();
  });

  it('synthesizes an id when missing and sets migrated flag', () => {
    const f = flag();
    const ann = parseAnnotation({ anchor: { line_start: 1 } }, f);
    expect(ann).not.toBeNull();
    expect(ann!.id).toMatch(/^[0-9a-f]{6}$/);
    expect(f.v).toBe(true);
  });

  it('defaults resolved=false and sets migrated flag when not a boolean', () => {
    const f = flag();
    const ann = parseAnnotation({ id: 'aaa111', anchor: { line_start: 1 }, resolved: 'yes' }, f);
    expect(ann!.resolved).toBe(false);
    expect(f.v).toBe(true);
  });

  it('does not flag migrated when only optional fields use defaults', () => {
    // id + resolved present; missing quote/comments shouldn't flip migrated.
    const f = flag();
    parseAnnotation(
      { id: 'aaa111', anchor: { line_start: 1, line_end: 1, char_start: 0, char_end: 0 }, resolved: false },
      f,
    );
    expect(f.v).toBe(false);
  });

  it('defaults anchor fields to 1/1/0/0 when nullish (and does NOT flag migrated for them)', () => {
    const f = flag();
    const ann = parseAnnotation({ id: 'aaa111', resolved: false, anchor: {} }, f);
    expect(ann!.anchor).toEqual({ line_start: 1, line_end: 1, char_start: 0, char_end: 0 });
  });

  // BUG candidate: non-numeric strings leak NaN through Number().
  it('does NOT leak NaN when anchor fields are non-numeric strings', () => {
    const ann = parseAnnotation(
      { id: 'aaa111', resolved: false, anchor: { line_start: 'abc', line_end: 'def', char_start: 'g', char_end: 'h' } },
      flag(),
    );
    expect(ann).not.toBeNull();
    expect(Number.isFinite(ann!.anchor.line_start)).toBe(true);
    expect(Number.isFinite(ann!.anchor.line_end)).toBe(true);
    expect(Number.isFinite(ann!.anchor.char_start)).toBe(true);
    expect(Number.isFinite(ann!.anchor.char_end)).toBe(true);
  });

  it('flags migrated when anchor fields are numeric strings instead of numbers', () => {
    const f = flag();
    const ann = parseAnnotation(
      { id: 'aaa111', resolved: false, anchor: { line_start: '5', line_end: '5', char_start: '0', char_end: '0' } },
      f,
    );
    expect(ann).not.toBeNull();
    // Anchor fields should be real numbers (fallback values), not coerced strings.
    expect(typeof ann!.anchor.line_start).toBe('number');
    expect(Number.isFinite(ann!.anchor.line_start)).toBe(true);
    // The wrong-typed input should have triggered the migration flag so the file gets rewritten.
    expect(f.v).toBe(true);
  });

  it('filters out non-object entries in comments array', () => {
    const ann = parseAnnotation(
      {
        id: 'aaa111',
        resolved: false,
        anchor: { line_start: 1 },
        comments: ['not a comment', 42, null, { author: 'You', body: 'b', timestamp: 't' }],
      },
      flag(),
    );
    expect(ann!.comments).toHaveLength(1);
    expect(ann!.comments[0]).toEqual({ author: 'You', body: 'b', timestamp: 't' });
  });

  it('falls back to defaults for comment fields with wrong types', () => {
    const ann = parseAnnotation(
      {
        id: 'aaa111',
        resolved: false,
        anchor: { line_start: 1 },
        comments: [{ author: 42, body: null, timestamp: {} }],
      },
      flag(),
    );
    expect(ann!.comments[0].author).toBe('You');
    expect(ann!.comments[0].body).toBe('');
    // timestamp falls back to a fresh nowIso() — should be a parseable ISO string.
    expect(Number.isNaN(Date.parse(ann!.comments[0].timestamp))).toBe(false);
  });

  it('returns empty comments when comments is not an array', () => {
    const ann = parseAnnotation(
      { id: 'aaa111', resolved: false, anchor: { line_start: 1 }, comments: { not: 'array' } },
      flag(),
    );
    expect(ann!.comments).toEqual([]);
  });

  it('falls back to empty quote when quote is wrong type', () => {
    const ann = parseAnnotation(
      { id: 'aaa111', resolved: false, anchor: { line_start: 1 }, quote: 123 },
      flag(),
    );
    expect(ann!.quote).toBe('');
  });
});

// -----------------------------------------------------------------------------
// loadSidecar
// -----------------------------------------------------------------------------

describe('loadSidecar', () => {
  it('returns empty + non-migrated when sidecar does not exist', () => {
    expect(loadSidecar(srcPathIn('missing.ts'))).toEqual({ annotations: [], migrated: false });
  });

  it('returns empty + non-migrated when JSON is invalid', () => {
    const src = srcPathIn('x.ts');
    fs.writeFileSync(sidecarPathFor(src), '{not json');
    expect(loadSidecar(src)).toEqual({ annotations: [], migrated: false });
  });

  it('returns empty for top-level scalar', () => {
    const src = srcPathIn('x.ts');
    fs.writeFileSync(sidecarPathFor(src), '42');
    expect(loadSidecar(src)).toEqual({ annotations: [], migrated: false });
  });

  it('returns empty for top-level array', () => {
    const src = srcPathIn('x.ts');
    fs.writeFileSync(sidecarPathFor(src), '[]');
    expect(loadSidecar(src)).toEqual({ annotations: [], migrated: false });
  });

  it('returns empty when annotations is null', () => {
    const src = srcPathIn('x.ts');
    fs.writeFileSync(sidecarPathFor(src), JSON.stringify({ annotations: null }));
    expect(loadSidecar(src)).toEqual({ annotations: [], migrated: false });
  });

  it('returns empty when annotations is not an array', () => {
    const src = srcPathIn('x.ts');
    fs.writeFileSync(sidecarPathFor(src), JSON.stringify({ annotations: { bogus: true } }));
    expect(loadSidecar(src)).toEqual({ annotations: [], migrated: false });
  });

  it('keeps valid entries and skips invalid ones (mixed input)', () => {
    const src = srcPathIn('x.ts');
    const good: Annotation = makeAnn({ id: 'good01' });
    fs.writeFileSync(
      sidecarPathFor(src),
      JSON.stringify({
        annotations: [
          good,
          null,
          'string-not-allowed',
          { /* missing anchor */ id: 'bad01', resolved: false, comments: [] },
        ],
      }),
    );
    const { annotations } = loadSidecar(src);
    expect(annotations).toHaveLength(1);
    expect(annotations[0].id).toBe('good01');
  });

  it('signals migrated=true when an entry was missing id or resolved', () => {
    const src = srcPathIn('x.ts');
    fs.writeFileSync(
      sidecarPathFor(src),
      JSON.stringify({
        annotations: [
          { anchor: { line_start: 1, line_end: 1, char_start: 0, char_end: 0 } /* no id, no resolved */ },
        ],
      }),
    );
    const { annotations, migrated } = loadSidecar(src);
    expect(annotations).toHaveLength(1);
    expect(migrated).toBe(true);
  });

  it('does NOT signal migrated when sidecar is well-formed', () => {
    const src = srcPathIn('x.ts');
    writeSidecar(src, [makeAnn()]);
    expect(loadSidecar(src).migrated).toBe(false);
  });

  it('round-trips through writeSidecar', () => {
    const src = srcPathIn('x.ts');
    const ann = makeAnn();
    writeSidecar(src, [ann]);
    const { annotations, migrated } = loadSidecar(src);
    expect(annotations).toEqual([ann]);
    expect(migrated).toBe(false);
  });

  it('handles large sidecars (1000 annotations)', () => {
    const src = srcPathIn('big.ts');
    const list: Annotation[] = [];
    for (let i = 0; i < 1000; i++) {
      list.push(
        makeAnn({
          id: i.toString(16).padStart(6, '0'),
          anchor: { line_start: i + 1, line_end: i + 1, char_start: 0, char_end: 1 },
        }),
      );
    }
    writeSidecar(src, list);
    const { annotations } = loadSidecar(src);
    expect(annotations).toHaveLength(1000);
  });
});

// -----------------------------------------------------------------------------
// writeSidecar
// -----------------------------------------------------------------------------

describe('writeSidecar', () => {
  it('writes JSON with 2-space indent and trailing newline', () => {
    const src = srcPathIn('x.ts');
    writeSidecar(src, [makeAnn()]);
    const text = fs.readFileSync(sidecarPathFor(src), 'utf8');
    expect(text.endsWith('\n')).toBe(true);
    expect(text).toContain('  "annotations": [');
  });

  it('sorts annotations by (line_start, char_start) ascending', () => {
    const src = srcPathIn('x.ts');
    const a = makeAnn({ id: 'aaaaaa', anchor: { line_start: 10, line_end: 10, char_start: 0, char_end: 1 } });
    const b = makeAnn({ id: 'bbbbbb', anchor: { line_start: 5, line_end: 5, char_start: 0, char_end: 1 } });
    const c = makeAnn({ id: 'cccccc', anchor: { line_start: 5, line_end: 5, char_start: 4, char_end: 5 } });
    writeSidecar(src, [a, b, c]);
    const parsed = JSON.parse(fs.readFileSync(sidecarPathFor(src), 'utf8'));
    expect(parsed.annotations.map((x: Annotation) => x.id)).toEqual(['bbbbbb', 'cccccc', 'aaaaaa']);
  });

  it('overwrites an existing sidecar', () => {
    const src = srcPathIn('x.ts');
    writeSidecar(src, [makeAnn({ id: 'first1' })]);
    writeSidecar(src, [makeAnn({ id: 'secnd1' })]);
    const parsed = JSON.parse(fs.readFileSync(sidecarPathFor(src), 'utf8'));
    expect(parsed.annotations).toHaveLength(1);
    expect(parsed.annotations[0].id).toBe('secnd1');
  });

  it('creates parent directories if missing', () => {
    const src = path.join(tmp, 'nested', 'deep', 'x.ts');
    writeSidecar(src, [makeAnn()]);
    expect(fs.existsSync(sidecarPathFor(src))).toBe(true);
  });

  it('produces output whose top-level key is "annotations"', () => {
    const src = srcPathIn('x.ts');
    writeSidecar(src, []);
    const parsed = JSON.parse(fs.readFileSync(sidecarPathFor(src), 'utf8'));
    expect(Object.keys(parsed)).toEqual(['annotations']);
  });

  it('does not mutate the input array (sorts a copy)', () => {
    const src = srcPathIn('x.ts');
    const list = [
      makeAnn({ id: 'aaaaaa', anchor: { line_start: 10, line_end: 10, char_start: 0, char_end: 1 } }),
      makeAnn({ id: 'bbbbbb', anchor: { line_start: 5, line_end: 5, char_start: 0, char_end: 1 } }),
    ];
    const snapshot = list.map((a) => a.id);
    writeSidecar(src, list);
    expect(list.map((a) => a.id)).toEqual(snapshot);
  });

  it('writes an empty sidecar cleanly', () => {
    const src = srcPathIn('x.ts');
    writeSidecar(src, []);
    expect(fs.readFileSync(sidecarPathFor(src), 'utf8')).toBe('{\n  "annotations": []\n}\n');
  });

  it('preserves unknown top-level keys from an existing sidecar (forward-compat)', () => {
    const src = srcPathIn('x.ts');
    fs.writeFileSync(
      sidecarPathFor(src),
      JSON.stringify({
        schema_version: 2,
        meta: { source: 'external-tool', tags: ['a', 'b'] },
        annotations: [],
      }, null, 2) + '\n',
    );
    // Extras are captured at load time; writeSidecar replays them from the cache.
    loadSidecar(src);
    writeSidecar(src, [makeAnn({ id: 'aaa111' })]);
    const parsed = JSON.parse(fs.readFileSync(sidecarPathFor(src), 'utf8'));
    expect(parsed.schema_version).toBe(2);
    expect(parsed.meta).toEqual({ source: 'external-tool', tags: ['a', 'b'] });
    expect(parsed.annotations).toHaveLength(1);
    expect(parsed.annotations[0].id).toBe('aaa111');
  });

  it('silently overwrites a malformed existing sidecar', () => {
    const src = srcPathIn('x.ts');
    fs.writeFileSync(sidecarPathFor(src), '{not valid json');
    // Load shows the error and clears extras; subsequent write overwrites cleanly.
    loadSidecar(src);
    writeSidecar(src, [makeAnn({ id: 'newone' })]);
    const parsed = JSON.parse(fs.readFileSync(sidecarPathFor(src), 'utf8'));
    expect(Object.keys(parsed)).toEqual(['annotations']);
    expect(parsed.annotations[0].id).toBe('newone');
  });

  it('uses atomic write (tmp + rename) — no .tmp residue after success', () => {
    const src = srcPathIn('x.ts');
    writeSidecar(src, [makeAnn()]);
    const files = fs.readdirSync(path.dirname(sidecarPathFor(src)));
    expect(files.some((f) => f.endsWith('.tmp'))).toBe(false);
  });
});

// -----------------------------------------------------------------------------
// formatTimestamp
// -----------------------------------------------------------------------------

describe('formatTimestamp', () => {
  it('formats a valid ISO timestamp to a short locale string', () => {
    const out = formatTimestamp('2025-06-15T12:34:56.000Z');
    expect(typeof out).toBe('string');
    expect(out.length).toBeGreaterThan(0);
    expect(out).not.toBe('Invalid Date');
  });

  // BUG candidate: try/catch never fires because toLocaleString on an Invalid Date
  // returns the literal string 'Invalid Date' rather than throwing.
  it('falls back to the original string for an invalid timestamp', () => {
    const out = formatTimestamp('not-a-date');
    expect(out).toBe('not-a-date');
  });

  it('falls back gracefully for empty string', () => {
    const out = formatTimestamp('');
    // Should be either '' (raw) or some safe non-"Invalid Date" output.
    expect(out).not.toBe('Invalid Date');
  });

  it('handles a far-future date without crashing', () => {
    expect(() => formatTimestamp('9999-12-31T23:59:59.000Z')).not.toThrow();
  });

  it('handles a very old date without crashing', () => {
    expect(() => formatTimestamp('1000-01-01T00:00:00.000Z')).not.toThrow();
  });
});

// -----------------------------------------------------------------------------
// escapeHtml
// -----------------------------------------------------------------------------

describe('escapeHtml', () => {
  it('escapes all five HTML entities', () => {
    expect(escapeHtml(`<>&"'`)).toBe('&lt;&gt;&amp;&quot;&#39;');
  });

  it('escapes & first (does not double-escape sequentially-produced entities)', () => {
    // The implementation does &→&amp; first, which means a literal '<' becoming
    // '&lt;' won't have its '&' re-escaped. Verify.
    expect(escapeHtml('<a>')).toBe('&lt;a&gt;');
  });

  it('double-escapes already-escaped input (this is the correct policy)', () => {
    // escapeHtml is a *raw text* escaper; literal "&amp;" in user content
    // must become "&amp;amp;" on output so it renders as "&amp;".
    expect(escapeHtml('&amp;')).toBe('&amp;amp;');
  });

  it('passes unicode through unchanged', () => {
    expect(escapeHtml('héllo 世界 💀')).toBe('héllo 世界 💀');
  });

  it('returns empty string for empty input', () => {
    expect(escapeHtml('')).toBe('');
  });

  // BUG candidate: escapeHtml crashes when called with non-string (signature is `string`
  // but loose callers / corrupted data can pass null/undefined).
  it('does not crash when given a non-string (defensive)', () => {
    // @ts-expect-error - probing runtime behavior
    expect(() => escapeHtml(undefined)).not.toThrow();
    // @ts-expect-error
    expect(() => escapeHtml(null)).not.toThrow();
  });
});

// -----------------------------------------------------------------------------
// lensLabel
// -----------------------------------------------------------------------------

describe('lensLabel', () => {
  it('singular for exactly 1 comment', () => {
    expect(lensLabel(makeAnn({ comments: [{ author: 'You', body: 'x', timestamp: 't' }] }))).toBe('1 comment');
  });

  it('plural for 0 comments', () => {
    expect(lensLabel(makeAnn({ comments: [] }))).toBe('0 comments');
  });

  it('plural for >1 comments', () => {
    expect(
      lensLabel(
        makeAnn({
          comments: [
            { author: 'You', body: 'a', timestamp: 't' },
            { author: 'Claude', body: 'b', timestamp: 't' },
          ],
        }),
      ),
    ).toBe('2 comments');
  });

  it('does not vary by resolved state', () => {
    const c = [{ author: 'You', body: 'x', timestamp: 't' }];
    expect(lensLabel(makeAnn({ resolved: true, comments: c }))).toBe('1 comment');
    expect(lensLabel(makeAnn({ resolved: false, comments: c }))).toBe('1 comment');
  });
});

// -----------------------------------------------------------------------------
// buildWebviewHtml
// -----------------------------------------------------------------------------

describe('buildWebviewHtml', () => {
  const NONCE = 'TESTNONCE';

  it('includes nonce in CSP and script tag', () => {
    const html = buildWebviewHtml(makeAnn(), NONCE);
    expect(html).toContain(`script-src 'nonce-${NONCE}'`);
    expect(html).toContain(`<script nonce="${NONCE}">`);
  });

  it('shows "Resolve" label when unresolved, "Unresolve" when resolved', () => {
    expect(buildWebviewHtml(makeAnn({ resolved: false }), NONCE)).toContain('>Resolve<');
    expect(buildWebviewHtml(makeAnn({ resolved: true }), NONCE)).toContain('>Unresolve<');
  });

  it('escapes HTML in quote', () => {
    const html = buildWebviewHtml(makeAnn({ quote: '<script>alert(1)</script>' }), NONCE);
    expect(html).not.toContain('<script>alert(1)</script>');
    expect(html).toContain('&lt;script&gt;alert(1)&lt;/script&gt;');
  });

  it('escapes HTML in comment body', () => {
    const html = buildWebviewHtml(
      makeAnn({
        comments: [{ author: 'You', body: '<img src=x onerror=alert(1)>', timestamp: '2025-01-01T00:00:00.000Z' }],
      }),
      NONCE,
    );
    expect(html).not.toContain('<img src=x onerror=alert(1)>');
    expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;');
  });

  it('escapes HTML in author', () => {
    const html = buildWebviewHtml(
      makeAnn({
        comments: [{ author: '<b>x</b>', body: 'hi', timestamp: '2025-01-01T00:00:00.000Z' }],
      }),
      NONCE,
    );
    expect(html).toContain('&lt;b&gt;x&lt;/b&gt;');
  });

  it('escapes HTML in annotation id', () => {
    const html = buildWebviewHtml(makeAnn({ id: '<x>' }), NONCE);
    expect(html).toContain('&lt;x&gt;');
  });

  it('omits the quote block when quote is empty', () => {
    const html = buildWebviewHtml(makeAnn({ quote: '' }), NONCE);
    expect(html).not.toContain('class="quote"');
  });

  it('renders the quote block when quote is non-empty', () => {
    const html = buildWebviewHtml(makeAnn({ quote: 'hi' }), NONCE);
    expect(html).toContain('class="quote"');
  });

  it('renders the Claude class for comments authored by "Claude"', () => {
    const html = buildWebviewHtml(
      makeAnn({
        comments: [{ author: 'Claude', body: 'c', timestamp: '2025-01-01T00:00:00.000Z' }],
      }),
      NONCE,
    );
    expect(html).toContain('class="comment claude"');
  });

  it('renders the user class for non-Claude authors', () => {
    const html = buildWebviewHtml(
      makeAnn({
        comments: [{ author: 'You', body: 'c', timestamp: '2025-01-01T00:00:00.000Z' }],
      }),
      NONCE,
    );
    expect(html).toContain('class="comment user"');
  });

  // BUG candidate: formatTimestamp returns "Invalid Date" for bad timestamps;
  // that string ends up rendered in the webview.
  it('does not render the literal "Invalid Date" for malformed timestamps', () => {
    const html = buildWebviewHtml(
      makeAnn({
        comments: [{ author: 'You', body: 'b', timestamp: 'garbage' }],
      }),
      NONCE,
    );
    expect(html).not.toContain('Invalid Date');
  });

  it('produces an HTML5 doctype', () => {
    expect(buildWebviewHtml(makeAnn(), NONCE).startsWith('<!DOCTYPE html>')).toBe(true);
  });
});

// =============================================================================
// Python ↔ TS interop
//
// Each side operates on `<source>.annotations.json` files. These tests treat
// each side as opaque: the Python CLI is invoked via execFileSync, and the TS
// extension is exercised only through its exported helpers. Failures here
// indicate the two implementations have drifted on the sidecar contract.
// =============================================================================

describe('python ↔ ts interop', () => {
  const REPO_ROOT = path.resolve(__dirname, '..', '..');
  const CLI = path.join(REPO_ROOT, 'scripts', 'annotate.py');

  function py(args: string[], cwd?: string): string {
    return execFileSync('python3', [CLI, ...args], {
      cwd,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe'],
    });
  }

  let workdir: string;

  beforeEach(() => {
    workdir = fs.mkdtempSync(path.join(os.tmpdir(), 'annot-interop-'));
  });

  afterEach(() => {
    try {
      fs.rmSync(workdir, { recursive: true, force: true });
    } catch {
      /* ignore */
    }
  });

  function writeSrc(name: string, body: string): string {
    const p = path.join(workdir, name);
    fs.writeFileSync(p, body, 'utf8');
    return p;
  }

  describe('python → ts', () => {
    it('TS loads a freshly Python-created sidecar cleanly (migrated=false)', () => {
      const src = writeSrc('src.txt', 'hello world\nfoo bar baz\n');
      py(['create', '--quote', 'hello', '--body', 'first', src]);

      const { annotations, migrated } = loadSidecar(src);
      expect(migrated).toBe(false);
      expect(annotations).toHaveLength(1);

      const a = annotations[0];
      expect(a.id).toMatch(/^[0-9a-f]{6}$/);
      expect(a.quote).toBe('hello');
      expect(a.resolved).toBe(false);
      expect(a.anchor).toEqual({ line_start: 1, line_end: 1, char_start: 0, char_end: 5 });
      expect(a.comments).toHaveLength(1);
      expect(a.comments[0].author).toBe('Claude');
      expect(a.comments[0].body).toBe('first');
      expect(a.comments[0].timestamp).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
    });

    it('TS sees Python replies in order', () => {
      const src = writeSrc('src.txt', 'hello world\n');
      const sidecar = sidecarPathFor(src);
      py(['create', '--quote', 'hello', '--body', 'one', src]);
      const id = loadSidecar(src).annotations[0].id;
      py(['reply', '--id', id, '--body', 'two', sidecar]);
      py(['reply', '--id', id, '--body', 'three', sidecar]);

      const { annotations, migrated } = loadSidecar(src);
      expect(migrated).toBe(false);
      expect(annotations[0].comments.map((c) => c.body)).toEqual(['one', 'two', 'three']);
      expect(annotations[0].comments.every((c) => c.author === 'Claude')).toBe(true);
    });

    it('TS sees resolved=true after Python resolve, and false after --unresolve', () => {
      const src = writeSrc('src.txt', 'hello world\n');
      const sidecar = sidecarPathFor(src);
      py(['create', '--quote', 'hello', '--body', 'x', src]);
      const id = loadSidecar(src).annotations[0].id;

      py(['resolve', '--id', id, sidecar]);
      expect(loadSidecar(src).annotations[0].resolved).toBe(true);

      py(['resolve', '--id', id, '--unresolve', sidecar]);
      expect(loadSidecar(src).annotations[0].resolved).toBe(false);
    });

    it('TS preserves unicode quote (emoji + CJK) written by Python', () => {
      const src = writeSrc('uni.txt', 'héllo 🌍 wörld\n你好世界\n');
      py(['create', '--quote', 'héllo 🌍', '--body', 'unicode', src]);

      const { annotations, migrated } = loadSidecar(src);
      expect(migrated).toBe(false);
      expect(annotations[0].quote).toBe('héllo 🌍');
    });

    it('TS preserves a multi-line quote written by Python', () => {
      const src = writeSrc('ml.txt', 'line one here\nline two there\nline three\n');
      const quote = 'one here\nline two';
      py(['create', '--quote', quote, '--body', 'ml', src]);

      const { annotations } = loadSidecar(src);
      expect(annotations[0].quote).toBe(quote);
      expect(annotations[0].anchor.line_start).toBeLessThan(annotations[0].anchor.line_end);
    });

    it('TS loads multiple annotations from a single sidecar', () => {
      const src = writeSrc('multi.txt', 'alpha bravo\ncharlie delta\necho foxtrot\n');
      py(['create', '--quote', 'alpha', '--body', 'a', src]);
      py(['create', '--quote', 'charlie', '--body', 'c', src]);
      py(['create', '--quote', 'echo', '--body', 'e', src]);

      const { annotations, migrated } = loadSidecar(src);
      expect(migrated).toBe(false);
      expect(annotations).toHaveLength(3);
      expect(annotations.map((a) => a.quote).sort()).toEqual(['alpha', 'charlie', 'echo']);
      for (const a of annotations) {
        expect(a.id).toMatch(/^[0-9a-f]{6}$/);
      }
    });
  });

  describe('ts → python', () => {
    function tsAnnotation(overrides: Partial<Annotation> = {}): Annotation {
      return {
        id: 'abc123',
        anchor: { line_start: 1, line_end: 1, char_start: 0, char_end: 5 },
        quote: 'hello',
        resolved: false,
        comments: [{ author: 'You', body: 'from ts', timestamp: '2026-05-22T12:00:00.000Z' }],
        ...overrides,
      };
    }

    it('Python list does not error and shows the TS-written annotation', () => {
      const src = writeSrc('src.txt', 'hello world\n');
      writeSidecar(src, [tsAnnotation()]);

      const out = py(['list', workdir]);
      expect(out).toContain('abc123');
      expect(out).toContain('hello');
    });

    it('Python reply succeeds on a TS-written sidecar and round-trips back to TS', () => {
      const src = writeSrc('src.txt', 'hello world\n');
      const sidecar = sidecarPathFor(src);
      writeSidecar(src, [tsAnnotation()]);

      py(['reply', '--id', 'abc123', '--body', 'from python', sidecar]);

      const { annotations, migrated } = loadSidecar(src);
      expect(migrated).toBe(false);
      expect(annotations[0].comments.map((c) => `${c.author}:${c.body}`)).toEqual([
        'You:from ts',
        'Claude:from python',
      ]);
    });

    it('Python list reflects the TS-set resolved state', () => {
      const src = writeSrc('src.txt', 'hello world\n');
      writeSidecar(src, [
        tsAnnotation({ id: 'aaa111', resolved: false }),
        tsAnnotation({
          id: 'bbb222',
          resolved: true,
          anchor: { line_start: 1, line_end: 1, char_start: 6, char_end: 11 },
          quote: 'world',
        }),
      ]);

      const out = py(['list', workdir]);
      const onlyOpen = py(['list', '--unresolved', workdir]);

      expect(out).toContain('aaa111');
      expect(out).toContain('bbb222');
      expect(onlyOpen).toContain('aaa111');
      expect(onlyOpen).not.toContain('bbb222');
    });
  });

  describe('round-trip stress', () => {
    it('Python create → TS load → TS write → Python list is lossless', () => {
      const src = writeSrc('src.txt', 'hello world\nfoo bar baz\n');
      py(['create', '--quote', 'hello', '--body', 'first', src]);
      py(['create', '--quote', 'foo bar', '--body', 'second', src]);

      const { annotations: before, migrated } = loadSidecar(src);
      expect(migrated).toBe(false);
      expect(before).toHaveLength(2);

      writeSidecar(src, before);

      const out = py(['list', workdir]);
      for (const a of before) {
        expect(out).toContain(a.id);
      }

      const { annotations: after, migrated: m2 } = loadSidecar(src);
      expect(m2).toBe(false);
      expect(after).toEqual(before);
    });

    it('TS write → Python reply → TS load picks up the Python comment', () => {
      const src = writeSrc('src.txt', 'hello world\n');
      const sidecar = sidecarPathFor(src);
      writeSidecar(src, [
        {
          id: 'deadbe',
          anchor: { line_start: 1, line_end: 1, char_start: 0, char_end: 5 },
          quote: 'hello',
          resolved: false,
          comments: [
            { author: 'You', body: 'ts initial', timestamp: '2026-05-22T10:00:00.000Z' },
          ],
        },
      ]);

      py(['reply', '--id', 'deadbe', '--body', 'python reply', sidecar]);

      const { annotations } = loadSidecar(src);
      expect(annotations[0].comments).toHaveLength(2);
      expect(annotations[0].comments[1].author).toBe('Claude');
      expect(annotations[0].comments[1].body).toBe('python reply');
    });
  });

  describe('boundary / adversarial', () => {
    it('annotation with empty comments array loads cleanly', () => {
      const src = writeSrc('src.txt', 'hello world\n');
      const sidecar = sidecarPathFor(src);
      const payload = {
        annotations: [
          {
            id: 'empty1',
            anchor: { line_start: 1, line_end: 1, char_start: 0, char_end: 5 },
            quote: 'hello',
            resolved: false,
            comments: [],
          },
        ],
      };
      fs.writeFileSync(sidecar, JSON.stringify(payload, null, 2) + '\n', 'utf8');

      const { annotations, migrated } = loadSidecar(src);
      expect(migrated).toBe(false);
      expect(annotations).toHaveLength(1);
      expect(annotations[0].comments).toEqual([]);
    });

    it('TS preserves unknown extra top-level keys on rewrite', () => {
      const src = writeSrc('src.txt', 'hello world\n');
      const sidecar = sidecarPathFor(src);
      const payload = {
        annotations: [
          {
            id: 'keep01',
            anchor: { line_start: 1, line_end: 1, char_start: 0, char_end: 5 },
            quote: 'hello',
            resolved: false,
            comments: [
              { author: 'You', body: 'b', timestamp: '2026-05-22T00:00:00.000Z' },
            ],
          },
        ],
        schema_version: 'v2-experimental',
        meta: { source: 'external-tool', tag: 'xyz' },
      };
      fs.writeFileSync(sidecar, JSON.stringify(payload, null, 2) + '\n', 'utf8');

      const { annotations } = loadSidecar(src);
      writeSidecar(src, annotations);

      const after = JSON.parse(fs.readFileSync(sidecar, 'utf8'));
      expect(after.schema_version).toBe('v2-experimental');
      expect(after.meta).toEqual({ source: 'external-tool', tag: 'xyz' });
    });

    it('sidecar format: 2-space indent + trailing newline (TS writes)', () => {
      const src = writeSrc('src.txt', 'hello world\n');
      const sidecar = sidecarPathFor(src);
      writeSidecar(src, [
        {
          id: 'fmt001',
          anchor: { line_start: 1, line_end: 1, char_start: 0, char_end: 5 },
          quote: 'hello',
          resolved: false,
          comments: [{ author: 'You', body: 'b', timestamp: '2026-05-22T00:00:00.000Z' }],
        },
      ]);
      const text = fs.readFileSync(sidecar, 'utf8');
      expect(text.endsWith('\n')).toBe(true);
      expect(text).toMatch(/\n  "annotations":/);
      expect(text).toMatch(/\n    \{/);
    });

    it('sidecar format: 2-space indent + trailing newline (Python writes)', () => {
      const src = writeSrc('src.txt', 'hello world\n');
      py(['create', '--quote', 'hello', '--body', 'x', src]);
      const text = fs.readFileSync(sidecarPathFor(src), 'utf8');
      expect(text.endsWith('\n')).toBe(true);
      expect(text).toMatch(/\n  "annotations":/);
      expect(text).toMatch(/\n    \{/);
    });
  });
});
